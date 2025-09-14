# gcs-server/git_status.py
"""
Git Status Polling System
=========================
Updated with intelligent logging - tracks git status without overwhelming
terminal output during routine polling operations.
"""

import threading
import time
import requests
from typing import Dict, Any
from config import load_config
from params import Params

# Import the new logging system
from logging_config import get_logger, log_system_error, log_system_warning

# Thread-safe data structures
git_status_data_all_drones = {}
git_status_stats = {}  # Track polling statistics
data_lock_git_status = threading.Lock()

def initialize_git_status_tracking(drones):
    """Initialize git status tracking for all drones"""
    logger = get_logger()
    
    with data_lock_git_status:
        for drone in drones:
            hw_id = drone['hw_id']
            git_status_data_all_drones[hw_id] = {}
            git_status_stats[hw_id] = {
                'success_count': 0,
                'failure_count': 0,
                'last_success': 0,
                'consecutive_failures': 0,
                'last_status': None
            }
    
    logger.log_system_event(
        f"Initialized git status tracking for {len(drones)} drones",
        "INFO", "git"
    )

def update_git_status_stats(drone_id: str, success: bool, status_data: Dict[str, Any] = None):
    """Update git status statistics for a drone"""
    with data_lock_git_status:
        stats = git_status_stats.get(drone_id, {})
        
        if success:
            stats['success_count'] = stats.get('success_count', 0) + 1
            stats['last_success'] = time.time()
            stats['consecutive_failures'] = 0
            if status_data:
                stats['last_status'] = status_data.get('status', 'unknown')
        else:
            stats['failure_count'] = stats.get('failure_count', 0) + 1
            stats['consecutive_failures'] = stats.get('consecutive_failures', 0) + 1
        
        git_status_stats[drone_id] = stats

def should_log_git_event(drone_id: str, success: bool, current_status: str = None) -> bool:
    """
    Ultra-quiet git logging decision - absolute minimum noise for production.
    Only logs critical git events and significant state changes.
    """
    from params import Params

    # Ultra-quiet mode - extremely selective logging
    if Params.ULTRA_QUIET_MODE:
        stats = git_status_stats.get(drone_id, {})
        consecutive_failures = stats.get('consecutive_failures', 0)

        if not success:
            # Only log after multiple failures to avoid single connection hiccups
            if consecutive_failures >= Params.MIN_ERROR_THRESHOLD:
                return consecutive_failures % Params.ERROR_REPORT_THROTTLE == 0
            return False

        # Log recovery only after significant failures, and only if enabled
        if not Params.SUPPRESS_RECOVERY_MESSAGES and consecutive_failures >= Params.MIN_ERROR_THRESHOLD:
            return True

        # Always log significant status changes (clean <-> dirty, branch changes)
        last_status = stats.get('last_status')
        if last_status and current_status and last_status != current_status:
            # But only if it's a meaningful change
            if (last_status == 'clean' and current_status == 'dirty') or \
               (last_status == 'dirty' and current_status == 'clean'):
                return True

        # Never log routine successful polls in ultra-quiet mode
        return False

    # Regular quiet mode behavior
    elif Params.POLLING_QUIET_MODE:
        stats = git_status_stats.get(drone_id, {})
        if not success:
            consecutive_failures = stats.get('consecutive_failures', 0)
            return consecutive_failures == 1 or consecutive_failures % Params.ERROR_REPORT_THROTTLE == 0
        if stats.get('consecutive_failures', 0) > 0:
            return True
        last_status = stats.get('last_status')
        if last_status and current_status and last_status != current_status:
            return True
        return False

    # Legacy behavior for verbose mode
    stats = git_status_stats.get(drone_id, {})
    if not success:
        return True
    if stats.get('consecutive_failures', 0) > 0:
        return True
    last_status = stats.get('last_status')
    if last_status and current_status and last_status != current_status:
        return True
    success_count = stats.get('success_count', 0)
    if success_count > 0 and success_count % 50 == 0:
        return True
    return False

def poll_git_status(drone):
    """
    Poll git status from a single drone with intelligent logging.
    Only logs significant events to reduce terminal noise.
    """
    drone_id = drone['hw_id']
    drone_ip = drone['ip']
    
    logger = get_logger()
    consecutive_errors = 0
    last_logged_error = None
    
    while True:
        try:
            # Construct git status URI
            full_uri = f"http://{drone_ip}:{Params.drones_flask_port}/get-git-status"
            
            # Make HTTP request
            response = requests.get(full_uri, timeout=Params.HTTP_REQUEST_TIMEOUT)

            if response.status_code == 200:
                git_data = response.json()
                
                # Update git status data
                with data_lock_git_status:
                    git_status_data_all_drones[drone_id] = git_data
                
                # Extract meaningful status information
                git_status = git_data.get('status', 'unknown')
                branch = git_data.get('branch', 'unknown')
                uncommitted_changes = git_data.get('uncommitted_changes', [])
                has_changes = len(uncommitted_changes) > 0
                
                # Update statistics
                update_git_status_stats(drone_id, True, git_data)
                
                # Reset consecutive errors on success
                if consecutive_errors > 0:
                    consecutive_errors = 0
                    # Log recovery
                    logger.log_drone_event(
                        drone_id, "git",
                        f"Git status restored: {git_status} on {branch}" + 
                        (f" ({len(uncommitted_changes)} uncommitted changes)" if has_changes else ""),
                        "INFO"
                    )
                
                # Log git status if it's significant
                elif should_log_git_event(drone_id, True, git_status):
                    status_msg = f"Git status: {git_status} on {branch}"
                    if has_changes:
                        status_msg += f" ({len(uncommitted_changes)} uncommitted changes)"
                    
                    # Determine log level based on git status
                    if git_status == 'dirty' or has_changes:
                        log_level = "WARNING"
                    else:
                        log_level = "INFO"
                    
                    logger.log_drone_event(drone_id, "git", status_msg, log_level, {
                        'branch': branch,
                        'status': git_status,
                        'uncommitted_count': len(uncommitted_changes),
                        'commit': git_data.get('commit', '')[:8] if git_data.get('commit') else 'unknown'
                    })

            else:
                # HTTP error
                error_msg = f"HTTP {response.status_code}: {response.text[:100]}"
                consecutive_errors += 1
                update_git_status_stats(drone_id, False)
                
                # Professional error logging with intelligent throttling
                if should_log_git_event(drone_id, False):
                    logger.log_drone_event(
                        drone_id, "git",
                        f"Git status request failed: {error_msg}",
                        "ERROR", {
                            'consecutive_errors': consecutive_errors,
                            'http_status': response.status_code,
                            'target_uri': full_uri
                        }
                    )
                    last_logged_error = error_msg

        except requests.Timeout:
            consecutive_errors += 1
            update_git_status_stats(drone_id, False)
            
            # Log timeout errors with smart throttling
            if should_log_git_event(drone_id, False):
                logger.log_drone_event(
                    drone_id, "git",
                    f"Git status timeout after {Params.HTTP_REQUEST_TIMEOUT}s",
                    "ERROR", {
                        'consecutive_errors': consecutive_errors,
                        'target_uri': full_uri
                    }
                )
                
        except requests.ConnectionError as e:
            consecutive_errors += 1
            update_git_status_stats(drone_id, False)
            
            # Log connection errors with professional throttling
            if should_log_git_event(drone_id, False):
                logger.log_drone_event(
                    drone_id, "git",
                    f"Git status connection failed to {drone_ip}",
                    "ERROR", {
                        'consecutive_errors': consecutive_errors,
                        'details': str(e)[:100]
                    }
                )
                
        except Exception as e:
            consecutive_errors += 1
            update_git_status_stats(drone_id, False)
            
            # Log unexpected errors immediately
            logger.log_drone_event(
                drone_id, "git",
                f"Unexpected error during git status polling: {type(e).__name__}",
                "ERROR", {
                    'consecutive_errors': consecutive_errors,
                    'details': str(e)[:100]
                }
            )

        # Wait before next poll
        time.sleep(Params.polling_interval)

def start_git_status_polling(drones):
    """Start git status polling threads for all drones with professional reporting"""
    if not drones:
        log_system_error("Cannot start git status polling: no drones provided", "git")
        return

    # Initialize tracking
    initialize_git_status_tracking(drones)

    logger = get_logger()
    started_threads = 0

    # Start polling threads
    for drone in drones:
        try:
            thread = threading.Thread(
                target=poll_git_status,
                args=(drone,),
                name=f"git-status-{drone['hw_id']}",
                daemon=True
            )
            thread.start()
            started_threads += 1

        except Exception as e:
            log_system_error(
                f"Failed to start git status thread for drone {drone['hw_id']}: {e}",
                "git"
            )

    # Start periodic status reporter
    _start_git_status_reporter()

    logger.log_system_event(
        f"Started {started_threads}/{len(drones)} git status polling threads with professional reporting",
        "INFO" if started_threads == len(drones) else "WARNING",
        "git"
    )

def _start_git_status_reporter():
    """Start background thread for periodic git status reports"""
    def git_status_reporter():
        from params import Params
        logger = get_logger()

        while True:
            try:
                time.sleep(Params.GIT_STATUS_REPORT_INTERVAL)
                summary = get_git_status_summary()
                sync_status = check_git_sync_status()

                # Generate professional status report
                active = summary['active_drones']
                total = summary['total_drones']
                failed = summary['failed_drones']
                dirty = summary['dirty_drones']

                # Sync status
                is_synced = sync_status['is_fully_synced']
                branch_count = len(sync_status['branch_distribution'])
                commit_count = len(sync_status['commit_distribution'])

                # Main status message
                if failed > 0:
                    level = "WARNING"
                    status = f"⚠️  GIT STATUS: {active}/{total} active, {failed} failed, {dirty} uncommitted"
                elif not is_synced:
                    level = "WARNING"
                    status = f"⚠️  GIT SYNC: {active}/{total} active, {branch_count} branches, {commit_count} commits"
                elif dirty > 0:
                    level = "INFO"
                    status = f"📋 GIT STATUS: {active}/{total} synced, {dirty} have uncommitted changes"
                else:
                    level = "INFO"
                    status = f"✅ GIT STATUS: All {total} drones synced and clean"

                # Only report if there are drones configured
                if total > 0:
                    logger.log_system_event(status, level, "git-report")

                # Additional details for issues
                if not is_synced and branch_count > 1:
                    branches_info = []
                    for branch, drone_list in sync_status['branch_distribution'].items():
                        branches_info.append(f"{branch}({len(drone_list)})")
                    logger.log_system_event(
                        f"Branch distribution: {', '.join(branches_info)}",
                        "WARNING", "git-report"
                    )

                # Report uncommitted changes
                if dirty > 0:
                    uncommitted = get_drones_with_uncommitted_changes()
                    drone_list = [f"D{d['drone_id']}({d['uncommitted_count']})"
                                  for d in uncommitted[:5]]
                    logger.log_system_event(
                        f"Uncommitted changes: {', '.join(drone_list)}{'...' if len(uncommitted) > 5 else ''}",
                        "INFO", "git-report"
                    )

            except Exception as e:
                logger.log_system_event(f"Git status reporter error: {e}", "ERROR", "git")
                time.sleep(60)  # Wait a minute before retrying

    reporter_thread = threading.Thread(target=git_status_reporter, daemon=True, name="git-status-reporter")
    reporter_thread.start()

def get_git_status_summary():
    """Get a summary of git status system health"""
    with data_lock_git_status:
        total_drones = len(git_status_stats)
        active_drones = 0
        dirty_drones = 0
        failed_drones = 0
        
        current_time = time.time()
        for drone_id, stats in git_status_stats.items():
            last_success = stats.get('last_success', 0)
            consecutive_failures = stats.get('consecutive_failures', 0)
            last_status = stats.get('last_status')
            
            if current_time - last_success < 120:  # Active if success within 2 minutes
                active_drones += 1
                if last_status == 'dirty':
                    dirty_drones += 1
            elif consecutive_failures > 5:  # Failed if 5+ consecutive failures
                failed_drones += 1
        
        return {
            'total_drones': total_drones,
            'active_drones': active_drones,
            'dirty_drones': dirty_drones,
            'failed_drones': failed_drones,
            'inactive_drones': total_drones - active_drones - failed_drones
        }

def get_drones_with_uncommitted_changes():
    """Get list of drones with uncommitted git changes"""
    drones_with_changes = []
    
    with data_lock_git_status:
        for drone_id, git_data in git_status_data_all_drones.items():
            if git_data.get('status') == 'dirty' or git_data.get('uncommitted_changes'):
                uncommitted_changes = git_data.get('uncommitted_changes', [])
                drones_with_changes.append({
                    'drone_id': drone_id,
                    'branch': git_data.get('branch', 'unknown'),
                    'uncommitted_count': len(uncommitted_changes),
                    'changes': uncommitted_changes
                })
    
    return drones_with_changes

def check_git_sync_status():
    """
    Check if all drones are on the same git commit and branch.
    Returns dict with sync status information.
    """
    branches = {}
    commits = {}
    
    with data_lock_git_status:
        for drone_id, git_data in git_status_data_all_drones.items():
            if git_data:  # Only check drones with valid git data
                branch = git_data.get('branch', 'unknown')
                commit = git_data.get('commit', 'unknown')
                
                if branch not in branches:
                    branches[branch] = []
                branches[branch].append(drone_id)
                
                if commit not in commits:
                    commits[commit] = []
                commits[commit].append(drone_id)
    
    # Determine sync status
    branch_count = len(branches)
    commit_count = len(commits)
    
    is_branch_synced = branch_count <= 1
    is_commit_synced = commit_count <= 1
    
    sync_status = {
        'is_fully_synced': is_branch_synced and is_commit_synced,
        'is_branch_synced': is_branch_synced,
        'is_commit_synced': is_commit_synced,
        'branch_distribution': branches,
        'commit_distribution': commits,
        'total_active_drones': sum(len(drone_list) for drone_list in branches.values())
    }
    
    return sync_status

# Background monitoring for git issues
def start_git_monitoring():
    """Start background monitoring for git sync issues"""
    def monitor_loop():
        logger = get_logger()
        last_alert_time = 0
        alert_interval = 300  # 5 minutes between alerts
        
        while True:
            try:
                current_time = time.time()
                
                # Check for uncommitted changes
                drones_with_changes = get_drones_with_uncommitted_changes()
                if drones_with_changes and (current_time - last_alert_time) > alert_interval:
                    logger.log_system_event(
                        f"Warning: {len(drones_with_changes)} drones have uncommitted changes",
                        "WARNING", "git"
                    )
                    last_alert_time = current_time
                
                # Check sync status
                sync_status = check_git_sync_status()
                if not sync_status['is_fully_synced'] and (current_time - last_alert_time) > alert_interval:
                    logger.log_system_event(
                        f"Warning: Drones not in sync - {len(sync_status['branch_distribution'])} branches, {len(sync_status['commit_distribution'])} commits",
                        "WARNING", "git"
                    )
                    last_alert_time = current_time
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.log_system_event(
                    f"Git monitoring error: {str(e)}",
                    "ERROR", "git"
                )
                time.sleep(60)
    
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    get_logger().log_system_event(
        "Git status monitoring started",
        "INFO", "git"
    )

# Standalone test mode
if __name__ == "__main__":
    import argparse
    from logging_config import initialize_logging, LogLevel, DisplayMode
    
    parser = argparse.ArgumentParser(description='Test git status polling system')
    parser.add_argument('--log-level', choices=['QUIET', 'NORMAL', 'VERBOSE', 'DEBUG'],
                       default='VERBOSE', help='Log level')
    parser.add_argument('--display-mode', choices=['DASHBOARD', 'STREAM', 'HYBRID'],
                       default='HYBRID', help='Display mode')
    args = parser.parse_args()
    
    # Initialize logging
    initialize_logging(
        LogLevel[args.log_level],
        DisplayMode[args.display_mode]
    )
    
    # Load drones and start polling
    drones = load_config()
    if not drones:
        print("No drones found in configuration!")
        sys.exit(1)
    
    print(f"Starting git status polling test for {len(drones)} drones...")
    start_git_status_polling(drones)
    start_git_monitoring()
    
    try:
        while True:
            time.sleep(10)
            summary = get_git_status_summary()
            sync_status = check_git_sync_status()
            print(f"Git Status Summary: {summary}")
            print(f"Git Sync Status: Fully synced = {sync_status['is_fully_synced']}")
    except KeyboardInterrupt:
        print("\nTest completed!")