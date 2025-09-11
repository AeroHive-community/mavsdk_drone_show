# gcs-server/routes.py
"""
GCS Server Routes - Complete Version
===================================
All original functionality preserved with new logging system integrated.
"""

import json
import os
import threading
import sys
import time
import traceback
import zipfile
import requests
from flask import Flask, jsonify, request, send_file, send_from_directory, current_app
import pandas as pd
from datetime import datetime

# Import existing modules (preserving all original functionality)
from telemetry import telemetry_data_all_drones, start_telemetry_polling, data_lock
from command import send_commands_to_all, send_commands_to_selected
from config import get_drone_git_status, get_gcs_git_report, load_config, save_config, load_swarm, save_swarm
from utils import allowed_file, clear_show_directories, git_operations, zip_directory
from params import Params
from get_elevation import get_elevation
from origin import compute_origin_from_drone, save_origin, load_origin, calculate_position_deviations
from network import get_network_info_for_all_drones
from heartbeat import handle_heartbeat_post, get_all_heartbeats
from git_status import git_status_data_all_drones, data_lock_git_status

# Import new logging system with fallback
try:
    from logging_config import get_logger, log_system_error, log_system_warning
    
    def log_system_event(message: str, level: str = "INFO", component: str = "system"):
        """Log system event using new logging system"""
        get_logger().log_system_event(message, level, component)
        
    def log_api_request(endpoint: str, method: str, status_code: int = 200):
        """Log API request with intelligent filtering"""
        logger = get_logger()
        routine_endpoints = ['/telemetry', '/ping', '/drone-heartbeat']
        
        if any(routine in endpoint for routine in routine_endpoints):
            # Only log errors or every 100th request for routine endpoints
            if status_code >= 400:
                logger.log_system_event(
                    f"{method} {endpoint} - {status_code}",
                    "ERROR" if status_code >= 500 else "WARNING",
                    "api"
                )
        else:
            # Log all non-routine requests
            level = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
            logger.log_system_event(f"{method} {endpoint} - {status_code}", level, "api")
        
    LOGGING_AVAILABLE = True
except ImportError:
    # Fallback to standard logging
    import logging
    logger = logging.getLogger(__name__)
    
    def log_system_error(message: str, component: str = "system"):
        logger.error(f"[{component}] {message}")
        
    def log_system_warning(message: str, component: str = "system"):
        logger.warning(f"[{component}] {message}")
        
    def log_system_event(message: str, level: str = "INFO", component: str = "system"):
        logger.log(getattr(logging, level, logging.INFO), f"[{component}] {message}")
        
    def log_api_request(endpoint: str, method: str, status_code: int = 200):
        if status_code >= 400:
            logger.log(logging.ERROR if status_code >= 500 else logging.WARNING, 
                      f"API {method} {endpoint} - {status_code}")
        
    LOGGING_AVAILABLE = False

# Configure base directories (preserving original logic)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

if Params.sim_mode:
    plots_directory = os.path.join(BASE_DIR, 'shapes_sitl/swarm/plots')
    skybrush_dir = os.path.join(BASE_DIR, 'shapes_sitl/swarm/skybrush')
    processed_dir = os.path.join(BASE_DIR, 'shapes_sitl/swarm/processed')
    shapes_dir = os.path.join(BASE_DIR, 'shapes_sitl')
else:
    plots_directory = os.path.join(BASE_DIR, 'shapes/swarm/plots')
    skybrush_dir = os.path.join(BASE_DIR, 'shapes/swarm/skybrush')
    processed_dir = os.path.join(BASE_DIR, 'shapes/swarm/processed')
    shapes_dir = os.path.join(BASE_DIR, 'shapes')

sys.path.append(BASE_DIR)
from process_formation import run_formation_process

# Import new comprehensive metrics engine (after BASE_DIR is defined)
try:
    sys.path.append(os.path.join(BASE_DIR, 'functions'))
    from drone_show_metrics import DroneShowMetrics
    METRICS_AVAILABLE = True
except ImportError as e:
    METRICS_AVAILABLE = False
    # We'll log this later when logging is available

# Preserve original symbols and colors for backward compatibility
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
INFO_SYMBOL = BLUE + "ℹ️" + RESET
ERROR_SYMBOL = RED + "❌" + RESET

def error_response(message, status_code=500):
    """Generate a consistent error response with logging (preserving original)."""
    log_system_error(f"API Error {status_code}: {message}", "api")
    return jsonify({'status': 'error', 'message': message}), status_code

def success_response(data=None, message=None):
    """Generate consistent success response"""
    response = {
        'status': 'success',
        'timestamp': datetime.now().isoformat()
    }
    if message:
        response['message'] = message
    if data is not None:
        response['data'] = data
    return jsonify(response)

def setup_routes(app):
    """Setup all API routes (preserving ALL original functionality)"""
    
    @app.before_request
    def before_request():
        """Log request start"""
        request.start_time = time.time()

    @app.after_request  
    def after_request(response):
        """Log request completion"""
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            log_api_request(request.path, request.method, response.status_code)
        return response

    # ========================================================================
    # TELEMETRY ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/telemetry', methods=['GET'])
    def get_telemetry():
        if LOGGING_AVAILABLE:
            logger = get_logger()
            logger.log_system_event("Telemetry data requested", "INFO", "api")
            if not telemetry_data_all_drones:
                logger.log_system_event("Telemetry data is currently empty", "WARNING", "api")
        else:
            logger.info(f"{INFO_SYMBOL} Telemetry data requested")
            if not telemetry_data_all_drones:
                logger.warning(f"{YELLOW}Telemetry data is currently empty{RESET}")
        return jsonify(telemetry_data_all_drones)

    # ========================================================================
    # COMMAND ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/submit_command', methods=['POST'])
    def submit_command():
        """
        Endpoint to receive commands from the frontend and process them asynchronously.
        """
        command_data = request.get_json()
        if not command_data:
            return error_response("No command data provided", 400)

        # Extract target_drones from command_data if provided
        target_drones = command_data.pop('target_drones', None)

        log_system_event(f"Received command: {command_data} for drones: {target_drones}", "INFO", "command")

        try:
            drones = load_config()
            if not drones:
                return error_response("No drones found in the configuration", 500)

            # Start processing the command in a new thread
            if target_drones:
                thread = threading.Thread(target=process_command_async, args=(drones, command_data, target_drones))
            else:
                thread = threading.Thread(target=process_command_async, args=(drones, command_data))

            thread.daemon = True
            thread.start()

            log_system_event("Command processing started asynchronously.", "INFO", "command")
            
            response_data = {
                'status': 'success',
                'message': "Command received and is being processed."
            }
            return jsonify(response_data), 200
        except Exception as e:
            log_system_error(f"Error initiating command processing: {e}", "command")
            return error_response(f"Error initiating command processing: {e}")

    def process_command_async(drones, command_data, target_drones=None):
        """
        Function to process the command asynchronously (preserving original).
        """
        try:
            start_time = time.time()

            # Choose appropriate sending function based on target_drones
            if target_drones:
                results = send_commands_to_selected(drones, command_data, target_drones)
                total_count = len(target_drones)
            else:
                results = send_commands_to_all(drones, command_data)
                total_count = len(drones)

            elapsed_time = time.time() - start_time
            success_count = sum(results.values())

            log_system_event(f"Command sent to {success_count}/{total_count} drones in {elapsed_time:.2f} seconds", "INFO", "command")
        except Exception as e:
            log_system_error(f"Error processing command asynchronously: {e}", "command")

    # ========================================================================
    # CONFIGURATION ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/save-config-data', methods=['POST'])
    def save_config_route():
        config_data = request.get_json()
        if not config_data:
            return error_response("No configuration data provided", 400)

        log_system_event("Received configuration data for saving", "INFO", "config")

        try:
            # Validate config_data
            if not isinstance(config_data, list) or not all(isinstance(drone, dict) for drone in config_data):
                raise ValueError("Invalid configuration data format")

            # Save the configuration data
            save_config(config_data)
            log_system_event("Configuration saved successfully", "INFO", "config")

            git_info = None
            # If auto push to Git is enabled, perform Git operations
            if Params.GIT_AUTO_PUSH:
                log_system_event("Git auto-push is enabled. Attempting to push configuration changes to repository.", "INFO", "config")
                git_result = git_operations(
                    BASE_DIR,
                    f"Update configuration: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                if git_result.get('success'):
                    log_system_event("Git operations successful.", "INFO", "config")
                else:
                    log_system_error(f"Git operations failed: {git_result.get('message')}", "config")
                git_info = git_result

            # Return a success message, including Git info if applicable
            response_data = {'status': 'success', 'message': 'Configuration saved successfully'}
            if git_info:
                response_data['git_info'] = git_info

            return jsonify(response_data)
        except Exception as e:
            log_system_error(f"Error saving configuration: {e}", "config")
            return error_response(f"Error saving configuration: {e}")

    @app.route('/get-config-data', methods=['GET'])
    def get_config():
        log_system_event("Configuration data requested", "INFO", "config")
        try:
            config = load_config()
            return jsonify(config)
        except Exception as e:
            return error_response(f"Error loading configuration: {e}")

    # ========================================================================
    # SWARM ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/save-swarm-data', methods=['POST'])
    def save_swarm_route():
        swarm_data = request.get_json()
        if not swarm_data:
            return error_response("No swarm data provided", 400)

        log_system_event("Received swarm data for saving", "INFO", "swarm")
        try:
            save_swarm(swarm_data)
            log_system_event("Swarm data saved successfully", "INFO", "swarm")

            # Determine Git push behavior
            commit_override = request.args.get('commit')
            should_commit = (
                commit_override.lower() == 'true'
                if commit_override is not None
                else Params.GIT_AUTO_PUSH
            )

            git_info = None
            if should_commit:
                log_system_event("Git commit & push triggered.", "INFO", "swarm")
                try:
                    git_result = git_operations(
                        BASE_DIR,
                        f"Update swarm data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    git_info = git_result
                    if git_result.get('success'):
                        log_system_event("Git operations successful.", "INFO", "swarm")
                    else:
                        log_system_error(f"Git operations failed: {git_result.get('message')}", "swarm")
                except Exception as git_exc:
                    log_system_error(f"Exception during Git operations: {str(git_exc)}", "swarm")
                    git_info = {'success': False, 'message': str(git_exc), 'output': ''}

            response = {'status': 'success', 'message': 'Swarm data saved successfully'}
            if git_info:
                response['git_info'] = git_info

            return jsonify(response), 200

        except Exception as e:
            log_system_error(f"Error saving swarm data: {str(e)}", "swarm")
            return error_response(str(e), 500)
    
    @app.route('/get-swarm-data', methods=['GET'])
    def get_swarm():
        log_system_event("Swarm data requested", "INFO", "swarm")
        try:
            swarm = load_swarm()
            return jsonify(swarm)
        except Exception as e:
            return error_response(f"Error loading swarm data: {e}")

    # ========================================================================
    # SHOW MANAGEMENT ENDPOINTS (preserving ALL original functionality)
    # ========================================================================
    
    @app.route('/import-show', methods=['POST'])
    def import_show():
        """
        Endpoint to handle the uploading and processing of drone show files:
          1) Clears the SITL or real show directories (depending on sim_mode).
          2) Saves the uploaded zip.
          3) Extracts it into the correct skybrush_dir.
          4) Calls run_formation_process.
          5) Optionally pushes changes to Git.
        """
        log_system_event("Show import requested", "INFO", "show")
        file = request.files.get('file')
        if not file or file.filename == '':
            log_system_warning("No file part or empty filename", "show")
            return error_response('No file part or empty filename', 400)

        try:
            # 1) Clear the correct SITL/real show directories
            clear_show_directories(BASE_DIR)

            # 2) Save the uploaded zip into a temp folder
            zip_path = os.path.join(BASE_DIR, 'temp', 'uploaded.zip')
            os.makedirs(os.path.dirname(zip_path), exist_ok=True)
            file.save(zip_path)

            # 3) Extract the zip into the correct skybrush folder
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(skybrush_dir)
            os.remove(zip_path)

            # 4) Process formation
            log_system_event(f"Starting process formation for files in {skybrush_dir}", "INFO", "show")
            output = run_formation_process(BASE_DIR)
            log_system_event(f"Process formation output: {output}", "INFO", "show")

            # 5) Calculate comprehensive metrics (new feature)
            comprehensive_metrics = None
            if METRICS_AVAILABLE:
                try:
                    log_system_event("Calculating comprehensive metrics", "INFO", "show")
                    metrics_engine = DroneShowMetrics(processed_dir)
                    comprehensive_metrics = metrics_engine.calculate_comprehensive_metrics()
                    
                    # Save metrics to file for later retrieval
                    metrics_file = metrics_engine.save_metrics_to_file(comprehensive_metrics)
                    if metrics_file:
                        log_system_event(f"Comprehensive metrics saved to {metrics_file}", "INFO", "show")
                    else:
                        log_system_warning("Failed to save comprehensive metrics", "show")
                except Exception as metrics_error:
                    log_system_error(f"Error calculating comprehensive metrics: {metrics_error}", "show")
                    comprehensive_metrics = {'error': str(metrics_error)}

            # 6) Optionally do Git commit/push
            if Params.GIT_AUTO_PUSH:
                log_system_event("Git auto-push is enabled. Attempting to push show changes to repository.", "INFO", "show")
                git_result = git_operations(
                    BASE_DIR,
                    f"Update from upload: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {file.filename}"
                )
                if git_result.get('success'):
                    log_system_event("Git operations successful.", "INFO", "show")
                else:
                    log_system_error(f"Git operations failed: {git_result.get('message')}", "show")
                
                response_data = {
                    'success': True, 
                    'message': output, 
                    'git_info': git_result
                }
                if comprehensive_metrics:
                    response_data['comprehensive_metrics'] = comprehensive_metrics
                
                return jsonify(response_data)
            else:
                response_data = {'success': True, 'message': output}
                if comprehensive_metrics:
                    response_data['comprehensive_metrics'] = comprehensive_metrics
                
                return jsonify(response_data)
        except Exception as e:
            log_system_error(f"Unexpected error during show import: {traceback.format_exc()}", "show")
            return error_response(f"Unexpected error during show import: {traceback.format_exc()}")

    @app.route('/download-raw-show', methods=['GET'])
    def download_raw_show():
        try:
            zip_file = zip_directory(skybrush_dir, os.path.join(BASE_DIR, 'temp/raw_show'))
            return send_file(zip_file, as_attachment=True, download_name='raw_show.zip')
        except Exception as e:
            return error_response(f"Error creating raw show zip: {e}")

    @app.route('/download-processed-show', methods=['GET'])
    def download_processed_show():
        try:
            zip_file = zip_directory(processed_dir, os.path.join(BASE_DIR, 'temp/processed_show'))
            return send_file(zip_file, as_attachment=True, download_name='processed_show.zip')
        except Exception as e:
            return error_response(f"Error creating processed show zip: {e}")
        
    @app.route('/get-show-info', methods=['GET'])
    def get_show_info():
        try:
            check_all = True

            # Find all Drone CSV files
            drone_csv_files = [f for f in os.listdir(skybrush_dir) 
                            if f.startswith('Drone ') and f.endswith('.csv')]
            
            if not drone_csv_files:
                return error_response("No drone CSV files found")

            # If check_all is False, filter to just "Drone 1.csv" (or first in the list)
            if not check_all:
                drone_csv_files = [drone_csv_files[0]]

            drone_count = len(drone_csv_files)

            max_duration_ms = 0.0
            max_altitude = 0.0

            # Iterate over each CSV to find the maximum duration and altitude
            for csv_file in drone_csv_files:
                csv_path = os.path.join(skybrush_dir, csv_file)

                with open(csv_path, 'r') as file:
                    # Skip the header
                    next(file)

                    lines = file.readlines()
                    if not lines:
                        continue

                    # Last line for time
                    last_line = lines[-1].strip().split(',')
                    duration_ms = float(last_line[0])
                    # Update max duration
                    if duration_ms > max_duration_ms:
                        max_duration_ms = duration_ms

                    # Find max altitude in this CSV
                    for line in lines:
                        parts = line.strip().split(',')
                        if len(parts) < 4:
                            continue
                        z_val = float(parts[3])  # 'z [m]' is the 4th column
                        if z_val > max_altitude:
                            max_altitude = z_val

            # Convert max duration to minutes / seconds
            duration_minutes = max_duration_ms / 60000
            duration_seconds = (max_duration_ms % 60000) / 1000
            
            return jsonify({
                'drone_count': drone_count,
                'duration_ms': max_duration_ms,
                'duration_minutes': round(duration_minutes, 2),
                'duration_seconds': round(duration_seconds, 2),
                'max_altitude': round(max_altitude, 2)
            })

        except FileNotFoundError:
            return error_response("Drone CSV files not found in skybrush directory")
        except Exception as e:
            return error_response(f"Error reading show info: {e}")

    @app.route('/get-comprehensive-metrics', methods=['GET'])
    def get_comprehensive_metrics():
        """
        NEW ENDPOINT: Retrieve comprehensive trajectory analysis metrics
        """
        log_system_event("Comprehensive metrics requested", "INFO", "show")
        
        if not METRICS_AVAILABLE:
            return error_response("Enhanced metrics engine not available", 503)
        
        try:
            # Try to load from saved file first (now in swarm directory)
            swarm_dir = os.path.join(BASE_DIR, base_folder, 'swarm') if 'base_folder' in locals() else os.path.join(BASE_DIR, 'shapes/swarm')
            metrics_file = os.path.join(swarm_dir, 'comprehensive_metrics.json')
            if os.path.exists(metrics_file):
                with open(metrics_file, 'r') as f:
                    metrics_data = json.load(f)
                log_system_event("Comprehensive metrics loaded from file", "INFO", "show")
                return jsonify(metrics_data)
            
            # If no saved file, calculate on-demand
            log_system_event("Calculating comprehensive metrics on-demand", "INFO", "show")
            metrics_engine = DroneShowMetrics(processed_dir)
            comprehensive_metrics = metrics_engine.calculate_comprehensive_metrics()
            
            # Save for future requests
            metrics_engine.save_metrics_to_file(comprehensive_metrics)
            
            return jsonify(comprehensive_metrics)
            
        except Exception as e:
            log_system_error(f"Error retrieving comprehensive metrics: {e}", "show")
            return error_response(f"Error calculating comprehensive metrics: {e}")

    @app.route('/get-safety-report', methods=['GET'])
    def get_safety_report():
        """
        NEW ENDPOINT: Get detailed safety analysis report
        """
        log_system_event("Safety report requested", "INFO", "show")
        
        if not METRICS_AVAILABLE:
            return error_response("Enhanced metrics engine not available", 503)
        
        try:
            metrics_engine = DroneShowMetrics(processed_dir)
            if not metrics_engine.load_drone_data():
                return error_response("No drone data available for safety analysis", 404)
            
            safety_metrics = metrics_engine.calculate_safety_metrics()
            
            return jsonify({
                'safety_analysis': safety_metrics,
                'recommendations': [
                    'Maintain minimum 2m separation between drones',
                    'Ensure ground clearance > 1m at all times',
                    'Monitor collision warnings during flight'
                ] if safety_metrics.get('collision_warnings_count', 0) > 0 else [
                    'Safety analysis complete - no issues detected',
                    'Formation maintains safe separation distances'
                ]
            })
            
        except Exception as e:
            log_system_error(f"Error generating safety report: {e}", "show")
            return error_response(f"Error generating safety report: {e}")

    @app.route('/validate-trajectory', methods=['POST'])
    def validate_trajectory():
        """
        NEW ENDPOINT: Real-time trajectory validation
        """
        log_system_event("Trajectory validation requested", "INFO", "show")
        
        if not METRICS_AVAILABLE:
            return error_response("Enhanced metrics engine not available", 503)
        
        try:
            metrics_engine = DroneShowMetrics(processed_dir)
            if not metrics_engine.load_drone_data():
                return error_response("No drone data available for validation", 404)
            
            # Calculate all metrics for validation
            all_metrics = metrics_engine.calculate_comprehensive_metrics()
            
            # Determine overall validation status
            validation_status = "PASS"
            issues = []
            
            if 'safety_metrics' in all_metrics:
                safety = all_metrics['safety_metrics']
                if safety.get('safety_status') != 'SAFE':
                    validation_status = "FAIL"
                    issues.append(f"Safety issue: {safety.get('safety_status')}")
                
                if safety.get('collision_warnings_count', 0) > 0:
                    validation_status = "WARNING"
                    issues.append(f"{safety['collision_warnings_count']} collision warnings")
            
            if 'performance_metrics' in all_metrics:
                perf = all_metrics['performance_metrics']
                if perf.get('max_velocity_ms', 0) > 15:  # 15 m/s limit
                    validation_status = "WARNING"
                    issues.append(f"High velocity: {perf['max_velocity_ms']} m/s")
            
            return jsonify({
                'validation_status': validation_status,
                'issues': issues,
                'metrics_summary': {
                    'safety_status': all_metrics.get('safety_metrics', {}).get('safety_status', 'Unknown'),
                    'max_velocity': all_metrics.get('performance_metrics', {}).get('max_velocity_ms', 0),
                    'formation_quality': all_metrics.get('formation_metrics', {}).get('formation_quality', 'Unknown')
                }
            })
            
        except Exception as e:
            log_system_error(f"Error validating trajectory: {e}", "show")
            return error_response(f"Error validating trajectory: {e}")

    @app.route('/deploy-show', methods=['POST'])
    def deploy_show():
        """
        NEW ENDPOINT: Deploy show changes to git repository for drone fleet
        """
        log_system_event("Show deployment requested", "INFO", "deploy")
        
        try:
            data = request.get_json() or {}
            commit_message = data.get('message', f"Deploy drone show: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Perform git operations to commit and push changes
            git_result = git_operations(BASE_DIR, commit_message)
            
            if git_result.get('success'):
                log_system_event("Show deployment successful", "INFO", "deploy")
                return jsonify({
                    'success': True,
                    'message': 'Show deployed successfully to drone fleet',
                    'git_info': git_result
                })
            else:
                log_system_error(f"Show deployment failed: {git_result.get('message')}", "deploy")
                return error_response(f"Deployment failed: {git_result.get('message')}")
                
        except Exception as e:
            log_system_error(f"Error during show deployment: {e}", "deploy")
            return error_response(f"Error during deployment: {e}")

    @app.route('/get-show-plots/<filename>')
    def send_image(filename):
        log_system_event(f"Image requested: {filename}", "INFO", "show")
        try:
            return send_from_directory(plots_directory, filename)
        except Exception as e:
            return error_response(f"Error sending image: {e}", 404)

    @app.route('/get-show-plots', methods=['GET'])
    def get_show_plots():
        log_system_event("Show plots list requested", "INFO", "show")
        try:
            if not os.path.exists(plots_directory):
                os.makedirs(plots_directory)

            filenames = [f for f in os.listdir(plots_directory) if f.endswith('.jpg')]
            upload_time = "unknown"
            if 'combined_drone_paths.png' in filenames:
                upload_time = time.ctime(os.path.getctime(os.path.join(plots_directory, 'combined_drone_paths.jpg')))

            return jsonify({'filenames': filenames, 'uploadTime': upload_time})
        except Exception as e:
            return error_response(f"Failed to list directory: {e}")

    @app.route('/get-custom-show-image', methods=['GET'])
    def get_custom_show_image():
        """
        Endpoint to get the custom drone show image.
        The image is expected to be located at shapes/active.png.
        """
        try:
            image_path = os.path.join(shapes_dir, 'trajectory_plot.png')
            print("Debug: Image path being used:", image_path)  # Debug statement
            if os.path.exists(image_path):
                return send_file(image_path, mimetype='image/png', as_attachment=False)
            else:
                return jsonify({'error': f'Custom show image not found at {image_path}'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ========================================================================
    # ELEVATION AND ORIGIN ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/elevation', methods=['GET'])
    def elevation_route():
        lat = request.args.get('lat')
        lon = request.args.get('lon')

        if lat is None or lon is None:
            log_system_error("Latitude and Longitude must be provided", "elevation")
            return jsonify({'error': 'Latitude and Longitude must be provided'}), 400

        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            log_system_error("Invalid latitude or longitude format", "elevation")
            return jsonify({'error': 'Invalid latitude or longitude format'}), 400

        elevation_data = get_elevation(lat, lon)
        if elevation_data:
            return jsonify(elevation_data)
        else:
            return jsonify({'error': 'Failed to fetch elevation data'}), 500

    @app.route('/set-origin', methods=['POST'])
    def set_origin():
        data = request.get_json()
        lat = data.get('lat')
        lon = data.get('lon')
        if lat is None or lon is None:
            log_system_error("Latitude and longitude are required", "origin")
            return jsonify({'status': 'error', 'message': 'Latitude and longitude are required'}), 400
        try:
            save_origin({'lat': lat, 'lon': lon})
            log_system_event("Origin coordinates saved", "INFO", "origin")
            return jsonify({'status': 'success', 'message': 'Origin saved'})
        except Exception as e:
            log_system_error(f"Error saving origin: {e}", "origin")
            return jsonify({'status': 'error', 'message': 'Error saving origin'}), 500

    @app.route('/get-origin', methods=['GET'])
    def get_origin():
        try:
            data = load_origin()
            if data['lat'] and data['lon']:
                return jsonify(data)
            else:
                return jsonify({'lat': None, 'lon': None})
        except Exception as e:
            log_system_error(f"Error loading origin: {e}", "origin")
            return jsonify({'status': 'error', 'message': 'Error loading origin'}), 500

    @app.route('/get-position-deviations', methods=['GET'])
    def get_position_deviations():
        """
        Endpoint to calculate the position deviations for all drones.
        """
        try:
            # Step 1: Get the origin coordinates
            origin = load_origin()
            if not origin or 'lat' not in origin or 'lon' not in origin or not origin['lat'] or not origin['lon']:
                return jsonify({"error": "Origin coordinates not set on GCS"}), 400
            origin_lat = float(origin['lat'])
            origin_lon = float(origin['lon'])

            # Step 2: Get the drones' configuration
            drones_config = load_config()
            if not drones_config:
                return jsonify({"error": "No drones configuration found"}), 500

            # Step 3: Get telemetry data with thread-safe access
            with data_lock:
                telemetry_data_copy = telemetry_data_all_drones.copy()

            # Step 4: Calculate deviations
            deviations = calculate_position_deviations(
                telemetry_data_copy, drones_config, origin_lat, origin_lon
            )

            # Step 5: Return deviations
            return jsonify(deviations), 200

        except Exception as e:
            log_system_error(f"Error in get_position_deviations: {e}", "origin")
            return jsonify({"error": str(e)}), 500

    @app.route('/compute-origin', methods=['POST'])
    def compute_origin():
        """
        Endpoint to compute the origin coordinates based on a drone's current position and intended N,E positions.
        """
        try:
            data = request.get_json()
            log_system_event(f"Received /compute-origin request data: {data}", "INFO", "origin")

            # Validate input data
            required_fields = ['current_lat', 'current_lon', 'intended_east', 'intended_north']
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                error_msg = f"Missing required field(s): {', '.join(missing_fields)}"
                log_system_error(error_msg, "origin")
                return jsonify({'status': 'error', 'message': error_msg}), 400

            # Parse and validate numerical inputs
            try:
                current_lat = float(data.get('current_lat'))
                current_lon = float(data.get('current_lon'))
                intended_east = float(data.get('intended_east'))
                intended_north = float(data.get('intended_north'))
            except (TypeError, ValueError) as e:
                error_msg = f"Invalid input data types: {e}"
                log_system_error(error_msg, "origin")
                return jsonify({'status': 'error', 'message': error_msg}), 400

            log_system_event(f"Parsed inputs - current_lat: {current_lat}, current_lon: {current_lon}, intended_east: {intended_east}, intended_north: {intended_north}", "INFO", "origin")

            # Compute the origin
            origin_lat, origin_lon = compute_origin_from_drone(current_lat, current_lon, intended_north, intended_east)

            # Save the origin
            save_origin({'lat': origin_lat, 'lon': origin_lon})

            return jsonify({'status': 'success', 'lat': origin_lat, 'lon': origin_lon}), 200

        except Exception as e:
            log_system_error(f"Error in compute_origin endpoint: {e}", "origin")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # ========================================================================
    # GIT STATUS ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/get-gcs-git-status', methods=['GET'])
    def get_gcs_git_status():
        """Retrieve the Git status of the GCS."""
        gcs_status = get_gcs_git_report()
        return jsonify(gcs_status)

    @app.route('/get-drone-git-status/<int:drone_id>', methods=['GET'])
    def fetch_drone_git_status(drone_id):
        """
        Endpoint to retrieve the Git status of a specific drone using its hardware ID (hw_id).
        :param drone_id: Hardware ID (hw_id) of the drone.
        :return: JSON response with Git status or an error message.
        """
        try:
            log_system_event(f"Fetching drone with ID {drone_id} from configuration", "DEBUG", "git")
            drones = load_config()
            drone = next((d for d in drones if int(d['hw_id']) == drone_id), None)

            if not drone:
                log_system_error(f'Drone with ID {drone_id} not found', "git")
                return jsonify({'error': f'Drone with ID {drone_id} not found'}), 404

            drone_uri = f"http://{drone['ip']}:{Params.drones_flask_port}"
            log_system_event(f"Constructed drone URI: {drone_uri}", "DEBUG", "git")
            drone_status = get_drone_git_status(drone_uri)

            if 'error' in drone_status:
                log_system_error(f"Error in drone status response: {drone_status['error']}", "git")
                return jsonify({'error': drone_status['error']}), 500

            log_system_event(f"Drone status retrieved successfully: {drone_status}", "DEBUG", "git")
            return jsonify(drone_status), 200
        except Exception as e:
            log_system_error(f"Exception occurred: {str(e)}", "git")
            return jsonify({'error': str(e)}), 500

    @app.route('/git-status', methods=['GET'])
    def get_git_status():
        """Endpoint to retrieve consolidated git status of all drones."""
        with data_lock_git_status:
            git_status_copy = git_status_data_all_drones.copy()
        return jsonify(git_status_copy)

    # ========================================================================
    # NETWORK AND HEARTBEAT ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/get-network-info', methods=['GET'])
    def get_network_info():
        """
        Endpoint to get network information for all drones.
        Each drone is queried individually, and the results are aggregated into a single JSON response.
        """
        # network_info, status_code = get_network_info_for_all_drones()
        # return jsonify(network_info), status_code
        pass

    @app.route('/drone-heartbeat', methods=['POST'])
    def drone_heartbeat():
        return handle_heartbeat_post()

    @app.route('/get-heartbeats', methods=['GET'])
    def get_heartbeats():
        return get_all_heartbeats()

    # ========================================================================
    # LEADER ELECTION ENDPOINTS (preserving original)
    # ========================================================================
    
    @app.route('/request-new-leader', methods=['POST'])
    def request_new_leader():
        """
        Called by a drone proposing a new leader.
        For now we auto-accept and update our local swarm.csv.
        """
        # 1. Parse and validate input JSON
        data = request.get_json()
        if not data or "hw_id" not in data:
            return error_response("Missing or invalid data: 'hw_id' is required", 400)

        hw_id = str(data["hw_id"])
        log_system_event(f"Received new-leader request from HW_ID={hw_id}", "INFO", "leader")

        try:
            # 2. Load the entire swarm table as a list of dicts
            swarm_data = load_swarm()  # returns List[Dict[str,str]]

            # 3. Locate the row matching our hw_id
            #    Using Python's next() with a generator to avoid a manual loop.
            entry = next((row for row in swarm_data if row.get('hw_id') == hw_id), None)
            if entry is None:
                # No match → return 404
                return error_response(f"HW_ID {hw_id} not found", 404)

            # 4. Update only the fields we care about.
            #    Use data.get(..., entry[field]) to preserve existing values if missing.
            entry['follow']     = data.get('follow',     entry['follow'])
            entry['offset_n']   = data.get('offset_n',   entry['offset_n'])
            entry['offset_e']   = data.get('offset_e',   entry['offset_e'])
            entry['offset_alt'] = data.get('offset_alt', entry['offset_alt'])
            # Convert the 'body_coord' flag from string to boolean
            entry['body_coord'] = (data.get('body_coord') == '1')

            # 5. Persist the updated list back to CSV
            #    → Ensure save_swarm() takes a List[Dict] and overwrites the file.
            save_swarm(swarm_data)

            # 6. Respond success
            return jsonify({
                'status':  'success',
                'message': f'Leader updated for HW_ID {hw_id}'
            })

        except Exception as e:
            # 7. On unexpected errors, log full traceback for debugging
            log_system_error(f"Error in request-new-leader: {e}", "leader")
            return error_response(f"Error processing request-new-leader: {e}", 500)

    # ========================================================================
    # SYSTEM STATUS ENDPOINTS
    # ========================================================================
    
    @app.route('/ping', methods=['GET'])
    def ping():
        """Simple endpoint to confirm connectivity."""
        return jsonify({"status": "ok"}), 200

    # Log successful route initialization
    log_system_event("All API routes initialized successfully", "INFO", "startup")
    
    # Log metrics engine availability
    if not METRICS_AVAILABLE:
        log_system_warning("Enhanced metrics engine not available - comprehensive analysis features disabled", "startup")