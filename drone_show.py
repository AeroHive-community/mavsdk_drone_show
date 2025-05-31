#!/usr/bin/env python3
"""
Drone Show Script (`drone_show.py`)

----------------------------------------
Author: Alireza Ghaderi
Date: 2025-01-29
Version: 2.6.0
----------------------------------------

Description:
    Orchestrates an offboard drone show by executing time-synchronized trajectories with optional
    local (NED) or global (LLH) setpoints. Integrates with MAVSDK for flight control, navpy for
    coordinate transforms, and an LED controller for real-time status indication.

Key Features:
  • Dual Setpoint Modes  
    – Local NED: `PositionNedYaw` for body-relative control.  
    – Global LLH: `PositionGlobalYaw` using GPS-derived LLA when `Params.USE_GLOBAL_SETPOINTS` is True.

  • Initial Climb Safety  
    – Vertical climb phase with configurable thresholds (altitude, duration, and mode) to avoid abrupt maneuvers.

  • Drift Compensation  
    – Time-drift catch-up allowing waypoint skipping and feed-forward velocity/acceleration in NED mode.

  • Auto / Manual Launch Position  
    – Auto-extract first waypoint origin or shift by preconfigured NED offsets.

  • Controlled vs. Native Landing  
    – Selects between PX4 native landing or a custom descent based on final altitude and mission progress.

  • Comprehensive Logging & LEDs  
    – Verbose debug/info logs and LED color changes reflecting initialization, pre-flight, in-flight, and error states.

Usage:
    python drone_show.py
        [--start_time START_TIME]          # UNIX timestamp to synchronize launch
        [--custom_csv CUSTOM_CSV]          # Trajectory file name (e.g., active.csv)
        [--auto_launch_position {True,False}]  # Override Params.AUTO_LAUNCH_POSITION
        [--debug]                          # Enable DEBUG log level

Command Line Arguments:
  --start_time              UNIX epoch time to delay mission start (default: now)
  --custom_csv              Custom CSV file under `shapes[_sitl]/`
  --auto_launch_position    Force enable/disable automatic origin extraction
  --debug                   Turn on detailed (DEBUG) logging

Dependencies:
  • Python 3.7+  
  • MAVSDK (`pip install mavsdk`)  
  • psutil (`pip install psutil`)  
  • tenacity (`pip install tenacity`)  
  • navpy (`pip install navpy`)  
  • requests, asyncio, argparse, logging, csv, socket

LED Status Indicators:
  • Blue      — Initialization  
  • Yellow    — Pre-flight checks  
  • White     — Offboard armed & ready  
  • Green     — Mission complete / standby  
  • Red       — Error or disarmed  

Notes:
  • Ensure `mavsdk_server` is executable and located at `~/mavsdk_drone_show/mavsdk_server`.  
  • Toggle `Params.USE_GLOBAL_SETPOINTS` to switch between local and global control modes.
"""


import os
import sys
import time
import asyncio
import csv
import subprocess
import logging
import socket
import psutil
import requests
import argparse
import pymap3d as pm

from collections import namedtuple

from mavsdk import System
import mavsdk
from mavsdk.offboard import (
    PositionNedYaw,
    VelocityBodyYawspeed,
    PositionGlobalYaw,
    VelocityNedYaw,
    AccelerationNed,
    OffboardError,
)
from mavsdk.telemetry import LandedState
from mavsdk.action import ActionError
from tenacity import retry, stop_after_attempt, wait_fixed

from src.led_controller import LEDController
from src.params import Params

from drone_show_src.utils import (
    configure_logging,
    read_hw_id,
    clamp_led_value,
    global_to_local,
)

# ----------------------------- #
#        Data Structures        #
# ----------------------------- #

Drone = namedtuple(
    "Drone",
    "hw_id pos_id initial_x initial_y ip mavlink_port debug_port gcs_ip",
)

# ----------------------------- #
#        Global Variables       #
# ----------------------------- #

HW_ID = None  # Hardware ID of the drone
position_id = None  # Position ID of the drone
global_synchronized_start_time = None  # Synchronized start time
initial_position_drift = None  # Initial position drift in NED coordinates
drift_delta = 0.0  # Drift delta (to adjust waypoint times)


CONFIG_CSV_NAME = os.path.join(Params.config_csv_name)

# ----------------------------- #
#         Helper Functions      #
# ----------------------------- #


def str2bool(v):
    """
    Convert a string to a boolean.

    Args:
        v (str): Input string.

    Returns:
        bool: Converted boolean value.

    Raises:
        argparse.ArgumentTypeError: If the input is not a valid boolean string.
    """
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def blender_north_west_up_to_ned(x_b, y_b, z_b=0.0):
    """
    Convert a 3D vector from a Blender-like system where:
      - X = North
      - Y = West
      - Z = Up
    into NED coordinates:
      - X = North
      - Y = East (so Y_ned = - Y_blender)
      - Z = Down (so Z_ned = - Z_blender)

    Args:
        x_b (float): Blender X (north)
        y_b (float): Blender Y (west)
        z_b (float): Blender Z (up)

    Returns:
        (float, float, float): (N, E, D) in NED
    """
    n = x_b          # North is unchanged
    e = -y_b         # West => negative East
    d = -z_b         # Up => negative Down
    return (n, e, d)


def read_config(filename: str) -> Drone:
    """
    Read the drone configuration from a CSV file.
    This CSV is assumed to store real NED coordinates directly:
      - initial_x => North
      - initial_y => East

    Args:
        filename (str): Path to the config CSV file.

    Returns:
        Drone: Namedtuple containing drone configuration if found, else None.
    """
    logger = logging.getLogger(__name__)
    try:
        with open(filename, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    hw_id = int(row["hw_id"])
                    if hw_id == HW_ID:
                        pos_id = int(row["pos_id"])
                        # For config.csv, we do NOT transform: it is already NED
                        initial_x = float(row["x"])  # North
                        initial_y = float(row["y"])  # East
                        ip = row["ip"]
                        mavlink_port = int(row["mavlink_port"])
                        debug_port = int(row["debug_port"])
                        gcs_ip = row["gcs_ip"]

                        drone = Drone(
                            hw_id,
                            pos_id,
                            initial_x,
                            initial_y,
                            ip,
                            mavlink_port,
                            debug_port,
                            gcs_ip,
                        )
                        logger.info(f"Drone configuration found: {drone}")
                        return drone
                except ValueError as ve:
                    logger.error(f"Invalid data type in config file row: {row}. Error: {ve}")
            logger.error(f"No configuration found for HW_ID {HW_ID}.")
            return None
    except FileNotFoundError:
        logger.exception("Config file not found.")
        return None
    except Exception:
        logger.exception(f"Error reading config file {filename}")
        return None


def extract_initial_positions(first_waypoint: dict) -> tuple:
    """
    Extract initial X, Y, Z positions from the first waypoint in NED coords.

    Args:
        first_waypoint (dict): Dictionary representing the first waypoint.

    Returns:
        tuple: (initial_x, initial_y, initial_z)

    Raises:
        KeyError: If required keys are missing in the waypoint.
        ValueError: If the values cannot be converted to float.
    """
    try:
        initial_x = float(first_waypoint["px"])
        initial_y = float(first_waypoint["py"])
        initial_z = float(first_waypoint.get("pz", 0.0))  # Default to 0.0 if pz not present
        return initial_x, initial_y, initial_z
    except KeyError as ke:
        raise KeyError(f"Missing key in first waypoint: {ke}")
    except ValueError as ve:
        raise ValueError(f"Invalid value in first waypoint: {ve}")


def adjust_waypoints(
    waypoints: list, initial_x: float, initial_y: float, initial_z: float = 0.0
) -> list:
    """
    Adjust all waypoints by subtracting the initial positions in NED coordinates.

    Args:
        waypoints (list): List of waypoints as tuples.
        initial_x (float): Initial X-coordinate (north).
        initial_y (float): Initial Y-coordinate (east).
        initial_z (float): Initial Z-coordinate (down).

    Returns:
        list: List of adjusted waypoints, such that the first position is (0,0,0).
    """
    adjusted_waypoints = []
    logger = logging.getLogger(__name__)

    for idx, waypoint in enumerate(waypoints):
        try:
            t, px, py, pz, vx, vy, vz, ax, ay, az, yaw, mode, ledr, ledg, ledb = waypoint
            adjusted_px = px - initial_x
            adjusted_py = py - initial_y
            adjusted_pz = pz - initial_z
            adjusted_waypoints.append(
                (
                    t,
                    adjusted_px,
                    adjusted_py,
                    adjusted_pz,
                    vx,
                    vy,
                    vz,
                    ax,
                    ay,
                    az,
                    yaw,
                    mode,
                    ledr,
                    ledg,
                    ledb,
                )
            )
        except ValueError as ve:
            logger.error(f"Error adjusting waypoint at index {idx}: {ve}")
        except Exception as e:
            logger.error(f"Unexpected error adjusting waypoint at index {idx}: {e}")

    return adjusted_waypoints


def read_trajectory_file(
    filename: str,
    auto_launch_position: bool = False,
    initial_x: float = 0.0,
    initial_y: float = 0.0
) -> list:
    """
    Read and adjust the trajectory waypoints from a CSV file.

    The CSV is assumed to be in a Blender-like coordinate system:
      - X = North
      - Y = West
      - Z = Up
    So we transform to real NED (X=north, Y=east, Z=down), then optionally shift
    so that the first point is (0,0,0) if auto_launch_position is True (or if we subtract config initial_x / initial_y).

    Args:
        filename (str): Path to the drone-specific trajectory CSV file.
        auto_launch_position (bool): Flag to determine if initial positions should be auto-extracted from the first waypoint.
        initial_x (float): Initial X (N) from config (if auto_launch_position=False).
        initial_y (float): Initial Y (E) from config (if auto_launch_position=False).

    Returns:
        list: List of adjusted waypoints in NED.
    """
    logger = logging.getLogger(__name__)
    waypoints = []

    try:
        with open(filename, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)

            if not rows:
                logger.error(f"Trajectory file '{filename}' is empty.")
                sys.exit(1)

            if auto_launch_position:
                # Extract initial positions from the first waypoint (in NED coords)
                try:
                    init_n, init_e, init_d = extract_initial_positions(rows[0])
                    logger.info(
                        f"Auto Launch Position ENABLED. NED for first waypoint: "
                        f"(N={init_n:.2f}, E={init_e:.2f}, D={init_d:.2f})"
                    )
                except (KeyError, ValueError) as e:
                    logger.error(f"Failed to extract initial positions: {e}")
                    sys.exit(1)

                # Read and collect all waypoints in NED
                for idx, row in enumerate(rows):
                    try:
                        t = float(row["t"])
                        # Positions in  NED (already NED)
                        px = float(row["px"])
                        py = float(row["py"])
                        pz = float(row.get("pz", 0.0))

                        # Velocities in NED (already NED)
                        vx = float(row["vx"])
                        vy = float(row["vy"])
                        vz = float(row["vz"])

                        # Accelerations in NED (already NED)
                        ax = float(row["ax"])
                        ay = float(row["ay"])
                        az = float(row["az"])

                        yaw = float(row["yaw"])
                        ledr = clamp_led_value(row.get("ledr", 0))
                        ledg = clamp_led_value(row.get("ledg", 0))
                        ledb = clamp_led_value(row.get("ledb", 0))
                        mode = row.get("mode", "0")

                        waypoints.append(
                            (
                                t,
                                px,
                                py,
                                pz,
                                vx,
                                vy,
                                vz,
                                ax,
                                ay,
                                az,
                                yaw,
                                mode,
                                ledr,
                                ledg,
                                ledb,
                            )
                        )
                    except ValueError as ve:
                        logger.error(f"Invalid data type in row {idx}: {row}. Error: {ve}")
                    except KeyError as ke:
                        logger.error(f"Missing key in row {idx}: {row}. Error: {ke}")

                # Adjust waypoints so the first point is (0,0,0) in NED
                waypoints = adjust_waypoints(waypoints, init_n, init_e, init_d)
                logger.info(f"Trajectory waypoints adjusted to start from NED (0, 0, 0).")

            else:
                # We already have initial_x, initial_y in real NED from config.
                init_d = 0.0  # Typically 0 if we only store 2D offsets

                for idx, row in enumerate(rows):
                    try:
                        t = float(row["t"])
                        px = float(row["px"])
                        py = float(row["py"])
                        pz = float(row["pz"])

                        vx = float(row["vx"])
                        vy = float(row["vy"])
                        vz = float(row["vz"])

                        ax = float(row["ax"])
                        ay = float(row["ay"])
                        az = float(row["az"])

                        yaw = float(row["yaw"])
                        ledr = clamp_led_value(row.get("ledr", 0))
                        ledg = clamp_led_value(row.get("ledg", 0))
                        ledb = clamp_led_value(row.get("ledb", 0))
                        mode = row.get("mode", "0")

                        waypoints.append(
                            (
                                t,
                                px,
                                py,
                                pz,
                                vx,
                                vy,
                                vz,
                                ax,
                                ay,
                                az,
                                yaw,
                                mode,
                                ledr,
                                ledg,
                                ledb,
                            )
                        )
                    except ValueError as ve:
                        logger.error(f"Invalid data type in row {idx}: {row}. Error: {ve}")
                    except KeyError as ke:
                        logger.error(f"Missing key in row {idx}: {row}. Error: {ke}")

                logger.info(f"Trajectory file '{filename}' read successfully with {len(waypoints)} waypoints.")

                # Now shift the entire path so that the first point is (0,0,0) in NED by subtracting (initial_x, initial_y, 0).
                waypoints = adjust_waypoints(waypoints, initial_x, initial_y, init_d)
                logger.info(
                    f"Trajectory waypoints adjusted using config initial positions "
                    f"(N={initial_x}, E={initial_y}, D={init_d})."
                )

    except FileNotFoundError:
        logger.exception(f"Trajectory file '{filename}' not found.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Error reading trajectory file '{filename}': {e}")
        sys.exit(1)

    return waypoints

# ----------------------------- #
#        Core Functionalities   #
# ----------------------------- #
async def perform_trajectory(
    drone: System,
    waypoints: list,
    home_position,
    start_time: float,
    launch_lat: float,
    launch_lon: float,
    launch_alt: float
):
    """
    Executes the trajectory with an initial vertical climb phase to prevent abrupt movements,
    and handles time-drift corrections after the initial climb is complete.

    Now supports both local NED and global LLA setpoints, chosen by Params.USE_GLOBAL_SETPOINTS.
    """
    global drift_delta, initial_position_drift
    logger = logging.getLogger(__name__)

    # Drift / sleep parameters
    DRIFT_CATCHUP_MAX_SEC = 0.5
    AHEAD_SLEEP_STEP_SEC   = 0.1

    total_waypoints = len(waypoints)
    waypoint_index  = 0
    landing_detected = False
    led_controller  = LEDController.get_instance()

    # Initial climb bookkeeping
    in_initial_climb         = True
    initial_climb_completed  = False
    initial_climb_start_time = time.time()
    initial_climb_yaw        = None

    # Time step between CSV rows (for drift‐skip calculations)
    csv_step = (waypoints[1][0] - waypoints[0][0]) \
        if total_waypoints > 1 else Params.DRIFT_CHECK_PERIOD

    # Determine final altitude to choose landing method
    final_altitude       = -waypoints[-1][3]  # Convert NED down to altitude
    trajectory_ends_high = final_altitude > Params.GROUND_ALTITUDE_THRESHOLD
    logger.info(
        f"Trajectory ends {'high' if trajectory_ends_high else 'low'}; "
        f"{'PX4 native landing' if trajectory_ends_high else 'controlled landing'}."
    )

    # -----------------------------------
    # Main Trajectory Execution Loop
    # -----------------------------------
    while waypoint_index < total_waypoints:
        try:
            now     = time.time()
            elapsed = now - start_time
            waypoint = waypoints[waypoint_index]
            t_wp    = waypoint[0]
            drift_delta = elapsed - t_wp

            # --- Case A: On time or behind schedule ---
            if drift_delta >= 0:
                # Unpack CSV row
                (_, raw_px, raw_py, raw_pz,
                 vx, vy, vz,
                 ax, ay, az,
                 raw_yaw, mode,
                 ledr, ledg, ledb) = waypoint

                # --- Apply initial-position correction if enabled ---
                if Params.ENABLE_INITIAL_POSITION_CORRECTION and initial_position_drift:
                    px = raw_px + initial_position_drift.north_m
                    py = raw_py + initial_position_drift.east_m
                    pz = raw_pz + initial_position_drift.down_m
                else:
                    px, py, pz = raw_px, raw_py, raw_pz

                # --- (1) Initial Climb Phase ---
                time_in_climb = now - initial_climb_start_time
                if not initial_climb_completed:
                    actual_alt = -pz
                    under_alt  = actual_alt < Params.INITIAL_CLIMB_ALTITUDE_THRESHOLD
                    under_time = time_in_climb < Params.INITIAL_CLIMB_TIME_THRESHOLD
                    in_initial_climb = under_alt or under_time
                    if not in_initial_climb:
                        initial_climb_completed = True
                else:
                    in_initial_climb = False

                # Update LED color for feedback
                led_controller.set_color(ledr, ledg, ledb)

                if in_initial_climb:
                    # BODY-frame climb or LOCAL-NED climb
                    if Params.INITIAL_CLIMB_MODE == "BODY_VELOCITY":
                        vz_climb = vz if abs(vz) > 1e-6 else Params.INITIAL_CLIMB_VZ_DEFAULT
                        if initial_climb_yaw is None:
                            initial_climb_yaw = raw_yaw if isinstance(raw_yaw, float) else 0.0
                        # Send body‐frame velocity setpoint
                        await drone.offboard.set_velocity_body(
                            VelocityBodyYawspeed(0.0, 0.0, -vz_climb, 0.0)
                        )
                    else:
                        if initial_climb_yaw is None:
                            async for e in drone.telemetry.attitude_euler():
                                initial_climb_yaw = e.yaw_deg
                                break
                        await drone.offboard.set_position_ned(
                            PositionNedYaw(px, py, pz, initial_climb_yaw)
                        )
                    waypoint_index += 1
                    continue

                # --- (2) Drift Correction (Skipping) ---
                safe_drift = min(drift_delta, DRIFT_CATCHUP_MAX_SEC)
                skip_count = int(safe_drift / csv_step)
                if skip_count > 0:
                    waypoint_index = min(waypoint_index + skip_count,
                                         total_waypoints - 1)
                    waypoint = waypoints[waypoint_index]
                    # Re-unpack after skip
                    (t_wp, raw_px, raw_py, raw_pz,
                     vx, vy, vz,
                     ax, ay, az,
                     raw_yaw, mode,
                     ledr, ledg, ledb) = waypoint

                    if Params.ENABLE_INITIAL_POSITION_CORRECTION and initial_position_drift:
                        px = raw_px + initial_position_drift.north_m
                        py = raw_py + initial_position_drift.east_m
                        pz = raw_pz + initial_position_drift.down_m
                    else:
                        px, py, pz = raw_px, raw_py, raw_pz

                # --- (3) Compute Altitude & Convert NED → LLA ---
                current_alt_sp = -pz
                # Use PyMap3D’s ned2geodetic (positional args):
                lla_lat, lla_lon, lla_alt = pm.ned2geodetic(
                    px, py, pz,
                    launch_lat, launch_lon, launch_alt
                )  # :contentReference[oaicite:15]{index=15}

                # --- (4) Global vs. Local Branching ---
                if Params.USE_GLOBAL_SETPOINTS:
                    # Send GLOBAL setpoint (lat, lon, alt, yaw)
                    gp = PositionGlobalYaw(
                    lla_lat,
                    lla_lon,
                    lla_alt,
                    raw_yaw,
                    PositionGlobalYaw.AltitudeType.AMSL
                    )
                    #Other Options: RELATIVE , AMSL , TAKEOFF
                    logger.debug(
                        f"GLOBAL setpoint → lat:{lla_lat:.6f}, lon:{lla_lon:.6f}, "
                        f"alt (AMSL):{lla_alt:.2f}, yaw:{raw_yaw:.1f}"
                    )
                    await drone.offboard.set_position_global(gp)
                else:
                    # Local NED setpoint
                    ln = PositionNedYaw(px, py, pz, raw_yaw)
                    logger.debug(
                        f"LOCAL NED setpoint → N:{px:.2f}, E:{py:.2f}, D:{pz:.2f}"
                    )

                    # Decide feedforward mode
                    if Params.FEEDFORWARD_VELOCITY_ENABLED and Params.FEEDFORWARD_ACCELERATION_ENABLED:
                        # Position+Velocity+Acceleration
                        velocity_setpoint     = VelocityNedYaw(vx, vy, vz, raw_yaw)
                        acceleration_setpoint = AccelerationNed(ax, ay, az)
                        await drone.offboard.set_position_velocity_acceleration_ned(
                            ln, velocity_setpoint, acceleration_setpoint
                        )
                    elif Params.FEEDFORWARD_VELOCITY_ENABLED:
                        # Position+Velocity only
                        velocity_setpoint = VelocityNedYaw(vx, vy, vz, raw_yaw)
                        await drone.offboard.set_position_velocity_ned(ln, velocity_setpoint)
                    else:
                        # Position‐only
                        await drone.offboard.set_position_ned(ln)

                led_controller.set_color(ledr, ledg, ledb)

                # --- (5) Progress & Landing Trigger ---
                time_to_end = waypoints[-1][0] - t_wp
                prog = (waypoint_index + 1) / total_waypoints
                logger.info(
                    f"WP {waypoint_index+1}/{total_waypoints}, "
                    f"progress {prog:.2%}, ETA {time_to_end:.2f}s, "
                    f"drift {drift_delta:.2f}s"
                )

                if (not trajectory_ends_high) and (prog >= Params.MISSION_PROGRESS_THRESHOLD):
                    if time_to_end <= Params.CONTROLLED_LANDING_TIME \
                       or current_alt_sp < Params.CONTROLLED_LANDING_ALTITUDE:
                        logger.info("Triggering controlled landing.")
                        await controlled_landing(drone)
                        landing_detected = True
                        break

                waypoint_index += 1

            # --- Case B: Ahead of schedule (drift_delta < 0) ---
            else:
                sleep_duration = t_wp - elapsed
                if sleep_duration > 0:
                    await asyncio.sleep(min(sleep_duration, AHEAD_SLEEP_STEP_SEC))
                else:
                    waypoint_index += 1

        except OffboardError as err:
            logger.error(f"Offboard error: {err}")
            led_controller.set_color(255, 0, 0)
            break
        except Exception:
            logger.exception("Error in trajectory loop.")
            led_controller.set_color(255, 0, 0)
            break

    # --- Post-trajectory Landing Handling ---
    if not landing_detected:
        if trajectory_ends_high:
            await stop_offboard_mode(drone)
            await perform_landing(drone)
            await wait_for_landing(drone)
        else:
            await controlled_landing(drone)

    logger.info("Mission complete.")
    led_controller.set_color(0, 255, 0)




async def controlled_landing(drone: System):
    """
    Perform controlled landing by sending descent commands and monitoring landing state.

    Args:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    logger.info("Initiating controlled landing.")
    led_controller = LEDController.get_instance()
    landing_detected = False
    landing_start_time = time.time()

    logger.info("Switching to controlled descent mode.")
    try:
        await drone.offboard.set_velocity_ned(
            VelocityNedYaw(0.0, 0.0, Params.CONTROLLED_DESCENT_SPEED, 0.0)
        )
    except OffboardError as e:
        logger.error(f"Offboard error during controlled landing setup: {e}")
        led_controller.set_color(255, 0, 0)
        return

    while not landing_detected:
        try:
            velocity_setpoint = VelocityNedYaw(0.0, 0.0, Params.CONTROLLED_DESCENT_SPEED, 0.0)
            await drone.offboard.set_velocity_ned(velocity_setpoint)
            logger.debug(f"Controlled Landing: Descending at {Params.CONTROLLED_DESCENT_SPEED:.2f} m/s.")
            async for landed_state in drone.telemetry.landed_state():
                if landed_state == LandedState.ON_GROUND:
                    landing_detected = True
                    logger.info("Landing detected during controlled landing.")
                    break
                break
            if (time.time() - landing_start_time) > Params.LANDING_TIMEOUT:
                logger.warning("Controlled landing timed out. Initiating PX4 native landing.")
                await stop_offboard_mode(drone)
                await perform_landing(drone)
                break
            await asyncio.sleep(0.1)
        except OffboardError as e:
            logger.error(f"Offboard error during controlled landing: {e}")
            led_controller.set_color(255, 0, 0)
            break
        except Exception:
            logger.exception("Unexpected error during controlled landing.")
            led_controller.set_color(255, 0, 0)
            break

    if landing_detected:
        await stop_offboard_mode(drone)
        await disarm_drone(drone)
    else:
        logger.warning("Landing not detected. Initiating PX4 native landing as fallback.")
        await stop_offboard_mode(drone)
        await perform_landing(drone)

    led_controller.set_color(0, 255, 0)


async def wait_for_landing(drone: System):
    """
    Wait for the drone to land after initiating landing.

    Args:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    start_time = time.time()
    logger.info("Waiting for drone to confirm landing...")
    while True:
        async for landed_state in drone.telemetry.landed_state():
            if landed_state == LandedState.ON_GROUND:
                logger.info("Drone has landed successfully.")
                return
            break
        if time.time() - start_time > Params.LANDING_TIMEOUT:
            logger.error("Landing confirmation timed out.")
            break
        await asyncio.sleep(1)



@retry(stop=stop_after_attempt(Params.PREFLIGHT_MAX_RETRIES), wait=wait_fixed(5))
async def initial_setup_and_connection():
    """
    Perform the initial setup and connection for the drone.

    Returns:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    try:
        # Initialize LEDController
        led_controller = LEDController.get_instance()
        # Set LED color to blue to indicate initialization
        led_controller.set_color(0, 0, 255)
        logger.info("LED set to blue: Initialization in progress.")

        # Determine the MAVSDK server address and port
        grpc_port = Params.DEFAULT_GRPC_PORT  # Fixed gRPC port

        # MAVSDK server is assumed to be running on localhost
        mavsdk_server_address = "127.0.0.1"

        # Create the drone system
        drone = System(mavsdk_server_address=mavsdk_server_address, port=grpc_port)
        await drone.connect(system_address=f"udp://:{Params.mavsdk_port}")

        logger.info(
            f"Connecting to drone via MAVSDK server at {mavsdk_server_address}:{grpc_port} on UDP port {Params.mavsdk_port}."
        )

        # Wait for connection with a timeout
        start_time = time.time()
        async for state in drone.core.connection_state():
            if state.is_connected:
                logger.info(
                    f"Drone connected via MAVSDK server at {mavsdk_server_address}:{grpc_port}."
                )
                break
            if time.time() - start_time > 10:
                logger.error("Timeout while waiting for drone connection.")
                led_controller.set_color(255, 0, 0)  # Red
                raise TimeoutError("Drone connection timeout.")
            await asyncio.sleep(1)

        # Log initial connection success
        logger.info("Initial setup and connection successful.")
        return drone
    except Exception:
        logger.exception("Error during initial setup and connection.")
        led_controller.set_color(255, 0, 0)  # Red
        raise



async def pre_flight_checks(drone: System):
    """
    Perform pre-flight checks to ensure the drone is ready for flight, including:
    - Checking the health of the global and home position via MAVSDK
    - Fetching the GPS global origin using MAVSDK when health is valid
    - Implementing a fallback mechanism using current position if origin request fails
    
    Args:
        drone (System): MAVSDK drone system instance

    Returns:
        dict: GPS global origin data containing latitude, longitude, and altitude
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting pre-flight checks.")

    led_controller = LEDController.get_instance()
    led_controller.set_color(255, 255, 0)  # Yellow = in progress

    start_time = time.time()
    gps_origin = None
    health_checks_passed = False

    try:
        if Params.REQUIRE_GLOBAL_POSITION:
            # Phase 1: Wait for health checks to pass with timeout
            logger.info("Waiting for health checks...")
            async for health in drone.telemetry.health():
                # Check timeout first
                if time.time() - start_time > Params.PRE_FLIGHT_TIMEOUT:
                    logger.error("Pre-flight checks timed out during health verification")
                    led_controller.set_color(255, 0, 0)
                    raise TimeoutError("Health check phase timed out")

                if health.is_global_position_ok and health.is_home_position_ok:
                    logger.info("Global position and home position checks passed")
                    health_checks_passed = True
                    break  # Exit health check loop

                # Log missing requirements
                if not health.is_global_position_ok:
                    logger.warning("Waiting for global position estimate...")
                if not health.is_home_position_ok:
                    logger.warning("Waiting for home position initialization...")
                
                await asyncio.sleep(1)

            if not health_checks_passed:
                raise RuntimeError("Failed to pass health checks")

            # Phase 2: Get GPS origin (with fallback)
            try:
                origin = await drone.telemetry.get_gps_global_origin()
                gps_origin = {
                    'latitude': origin.latitude_deg,
                    'longitude': origin.longitude_deg,
                    'altitude': origin.altitude_m
                }
                logger.info(f"Retrieved GPS global origin: {gps_origin}")
            except mavsdk.telemetry.TelemetryError as e:
                logger.warning(f"GPS origin request failed: {e}, using fallback...")
                # Get single position update as fallback
                async for position in drone.telemetry.position():
                    gps_origin = {
                        'latitude': position.latitude_deg,
                        'longitude': position.longitude_deg,
                        'altitude': position.absolute_altitude_m
                    }
                    logger.info(f"Using fallback position: {gps_origin}")
                    break  # Exit after first position update

            # Final validation
            if not gps_origin:
                logger.error("Failed to obtain GPS origin")
                led_controller.set_color(255, 0, 0)
                raise ValueError("No GPS origin available")

            logger.info("Pre-flight checks completed successfully")
            led_controller.set_color(0, 255, 0)  # Green = success
            return gps_origin

        else:
            logger.info("Skipping global position check per configuration")
            led_controller.set_color(0, 255, 0)
            return None

    except Exception as e:
        logger.exception("Critical error in pre-flight checks")
        led_controller.set_color(255, 0, 0)
        raise

@retry(stop=stop_after_attempt(Params.PREFLIGHT_MAX_RETRIES), wait=wait_fixed(2))
async def arming_and_starting_offboard_mode(drone: System, home_position: dict):
    """
    Arm the drone and start offboard mode, while computing the initial position offset in NED coordinates.

    Args:
        drone (System): MAVSDK drone system instance.
        home_position (dict): Home position telemetry data in global coordinates.

    Returns:
        None
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Initialize LED controller
        led_controller = LEDController.get_instance()
        led_controller.set_color(0, 255, 0)  # Green: Arming in progress
        logger.info("LED set to green: Arming in progress.")

        # Step 1: Compute initial position offset if required
        global initial_position_drift

        if Params.REQUIRE_GLOBAL_POSITION and home_position:
            logger.info("Computing initial position offset in NED coordinates.")
            initial_position_drift = await compute_position_drift()
            logger.info(f"Initial position drift computed: {initial_position_drift}")
        else:
            logger.info("Skipping position offset computation (global position check disabled or no home position).")

        # Step 2: Set flight mode to Hold as a safety precaution
        logger.info("Setting Hold flight mode.")
        await drone.action.hold()

        # Step 3: Arm the drone
        logger.info("Arming the drone.")
        await drone.action.arm()

        # Step 4: Set an initial offboard velocity setpoint
        logger.info("Setting initial velocity setpoint for offboard mode.")
        await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0.0, 0.0, 0.0, 0.0))

        # Step 5: Start offboard mode
        logger.info("Starting offboard mode.")
        await drone.offboard.start()

        # Indicate readiness with LED color
        led_controller.set_color(255, 255, 255)  # White: Ready to fly
        logger.info("LED set to white: Drone is ready to fly.")

    except OffboardError as error:
        # Handle specific Offboard mode errors
        logger.error(f"Offboard error encountered: {error}")
        await drone.action.disarm()
        led_controller.set_color(255, 0, 0)  # Red: Error state
        raise

    except Exception as e:
        # Handle general exceptions and ensure the drone is disarmed
        logger.exception("Unexpected error during arming and starting offboard mode.")
        await drone.action.disarm()
        led_controller.set_color(255, 0, 0)  # Red: Error state
        raise e



async def compute_position_drift():
    """
    Compute initial position drift using LOCAL_POSITION_NED from the drone's API.
    The NED origin is automatically set when the drone arms (matches GPS_GLOBAL_ORIGIN).
    
    Returns:
        PositionNedYaw: Drift in NED coordinates or None if unavailable
    """
    logger = logging.getLogger(__name__)
    default_drift = PositionNedYaw(0.0, 0.0, 0.0, 0.0)  # Default to no drift

    try:
        # Request NED data from local API endpoint
        response = requests.get(
            f"http://localhost:{Params.drones_flask_port}/get-local-position-ned",
            timeout=2
        )

        if response.status_code == 200:
            ned_data = response.json()
            
            # Create PositionNedYaw from API response (x=north, y=east, z=down)
            drift = PositionNedYaw(
                north_m=ned_data['x'],
                east_m=ned_data['y'],
                down_m=ned_data['z'],
                yaw_deg=0.0  # Adding yaw parameter, set to 0 if not needed
            )
            
            logger.info(f"Initial NED drift from origin: {drift}")
            return drift
        else:
            logger.warning(f"Failed to get NED data: HTTP {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"API connection failed: {str(e)}")
        return None
    except KeyError as e:
        logger.error(f"Malformed NED data: Missing field {str(e)}")
        return None


async def perform_landing(drone: System):
    """
    Perform landing for the drone.

    Args:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    try:
        logger.info("Initiating landing.")
        await drone.action.land()

        start_time = time.time()
        while True:
            async for landed_state in drone.telemetry.landed_state():
                if landed_state == LandedState.ON_GROUND:
                    logger.info("Drone has landed successfully.")
                    return
                break
            if time.time() - start_time > Params.LANDING_TIMEOUT:
                logger.error("Landing confirmation timed out.")
                break
            await asyncio.sleep(1)
    except ActionError as e:
        logger.error(f"Action error during landing: {e}")
    except Exception:
        logger.exception("Unexpected error during landing.")


async def stop_offboard_mode(drone: System):
    """
    Stop offboard mode for the drone.

    Args:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    try:
        logger.info("Stopping offboard mode.")
        await drone.offboard.stop()
    except OffboardError as error:
        logger.error(f"Error stopping offboard mode: {error}")
    except Exception:
        logger.exception("Unexpected error stopping offboard mode.")


async def disarm_drone(drone: System):
    """
    Disarm the drone.

    Args:
        drone (System): MAVSDK drone system instance.
    """
    logger = logging.getLogger(__name__)
    try:
        logger.info("Disarming drone.")
        await drone.action.disarm()
        # Set LEDs to solid red to indicate disarming
        led_controller = LEDController.get_instance()
        led_controller.set_color(255, 0, 0)
        logger.info("LED set to red: Drone disarmed.")
    except ActionError as e:
        logger.error(f"Error disarming drone: {e}")
    except Exception:
        logger.exception("Unexpected error disarming drone.")


# ----------------------------- #
#       MAVSDK Server Control   #
# ----------------------------- #


def get_mavsdk_server_path():
    """
    Constructs the absolute path to the mavsdk_server executable.

    Returns:
        str: Path to mavsdk_server.
    """
    home_dir = os.path.expanduser("~")
    mavsdk_drone_show_dir = os.path.join(home_dir, "mavsdk_drone_show")
    mavsdk_server_path = os.path.join(mavsdk_drone_show_dir, "mavsdk_server")
    return mavsdk_server_path


def start_mavsdk_server(udp_port: int):
    """
    Start MAVSDK server instance for the drone.

    Args:
        udp_port (int): UDP port for MAVSDK server communication.

    Returns:
        subprocess.Popen: MAVSDK server subprocess if started successfully, else None.
    """
    logger = logging.getLogger(__name__)
    try:
        # Check if MAVSDK server is already running
        is_running, pid = check_mavsdk_server_running(Params.DEFAULT_GRPC_PORT)
        if is_running:
            logger.info(f"MAVSDK server already running on port {Params.DEFAULT_GRPC_PORT}. Terminating existing server (PID: {pid})...")
            try:
                psutil.Process(pid).terminate()
                psutil.Process(pid).wait(timeout=5)
                logger.info(f"Terminated existing MAVSDK server with PID: {pid}.")
            except psutil.NoSuchProcess:
                logger.warning(f"No process found with PID: {pid} to terminate.")
            except psutil.TimeoutExpired:
                logger.warning(f"Process with PID: {pid} did not terminate gracefully. Killing it.")
                psutil.Process(pid).kill()
                psutil.Process(pid).wait()
                logger.info(f"Killed MAVSDK server with PID: {pid}.")

        # Construct the absolute path to mavsdk_server
        mavsdk_server_path = get_mavsdk_server_path()

        logger.debug(f"Constructed MAVSDK server path: {mavsdk_server_path}")

        if not os.path.isfile(mavsdk_server_path):
            logger.error(f"mavsdk_server executable not found at '{mavsdk_server_path}'.")
            sys.exit(1)  # Exit the program as the server is essential

        if not os.access(mavsdk_server_path, os.X_OK):
            logger.info(f"Setting executable permissions for '{mavsdk_server_path}'.")
            os.chmod(mavsdk_server_path, 0o755)

        # Start the MAVSDK server
        mavsdk_server = subprocess.Popen(
            [mavsdk_server_path, "-p", str(Params.DEFAULT_GRPC_PORT), f"udp://:{udp_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info(
            f"MAVSDK server started with gRPC port {Params.DEFAULT_GRPC_PORT} and UDP port {udp_port}."
        )

        # Optionally, start logging the MAVSDK server output asynchronously
        asyncio.create_task(log_mavsdk_output(mavsdk_server))

        # Wait until the server is listening on the gRPC port
        if not wait_for_port(Params.DEFAULT_GRPC_PORT, timeout=Params.PRE_FLIGHT_TIMEOUT):
            logger.error(
                f"MAVSDK server did not start listening on port {Params.DEFAULT_GRPC_PORT} within timeout."
            )
            mavsdk_server.terminate()
            return None

        logger.info("MAVSDK server is now listening on gRPC port.")
        return mavsdk_server

    except FileNotFoundError:
        logger.error("mavsdk_server executable not found. Ensure it is present in the specified directory.")
        return None
    except Exception:
        logger.exception("Error starting MAVSDK server.")
        return None


def check_mavsdk_server_running(port):
    """
    Checks if the MAVSDK server is running on the specified gRPC port.

    Args:
        port (int): The gRPC port to check.

    Returns:
        tuple: (is_running (bool), pid (int or None))
    """
    logger = logging.getLogger(__name__)
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.laddr.port == port:
                    return True, proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False, None


def wait_for_port(port, host="localhost", timeout=Params.PRE_FLIGHT_TIMEOUT):
    """
    Wait until a port starts accepting TCP connections.

    Args:
        port (int): The port to check.
        host (str): The hostname to check.
        timeout (float): The maximum time to wait in seconds.

    Returns:
        bool: True if the port is open, False if the timeout was reached.
    """
    start_time = time.time()
    while True:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            if time.time() - start_time >= timeout:
                return False
            time.sleep(0.1)


async def log_mavsdk_output(mavsdk_server):
    """
    Asynchronously logs the stdout and stderr of the MAVSDK server.

    Args:
        mavsdk_server (subprocess.Popen): The subprocess running the MAVSDK server.
    """
    logger = logging.getLogger(__name__)
    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, mavsdk_server.stdout.readline)
            if not line:
                break
            logger.debug(f"MAVSDK Server: {line.decode().strip()}")
    except Exception:
        logger.exception("Error while reading MAVSDK server stdout.")

    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, mavsdk_server.stderr.readline)
            if not line:
                break
            logger.error(f"MAVSDK Server Error: {line.decode().strip()}")
    except Exception:
        logger.exception("Error while reading MAVSDK server stderr.")


def stop_mavsdk_server(mavsdk_server):
    """
    Stop the MAVSDK server instance.

    Args:
        mavsdk_server (subprocess.Popen): MAVSDK server subprocess.
    """
    logger = logging.getLogger(__name__)
    try:
        if mavsdk_server.poll() is None:
            logger.info("Stopping MAVSDK server...")
            mavsdk_server.terminate()
            try:
                mavsdk_server.wait(timeout=5)
                logger.info("MAVSDK server terminated gracefully.")
            except subprocess.TimeoutExpired:
                logger.warning("MAVSDK server did not terminate gracefully. Killing it.")
                mavsdk_server.kill()
                mavsdk_server.wait()
                logger.info("MAVSDK server killed forcefully.")
        else:
            logger.debug("MAVSDK server has already terminated.")
    except Exception:
        logger.exception("Error stopping MAVSDK server.")


# ----------------------------- #
#         Main Drone Runner     #
# ----------------------------- #


async def run_drone(synchronized_start_time, custom_csv=None, auto_launch_position=False):
    """
    Run the drone with the provided configurations.

    Args:
        synchronized_start_time (float): Synchronized start time (UNIX timestamp).
        custom_csv (str): Name of the custom trajectory CSV file.
        auto_launch_position (bool): Flag to enable automated initial position extraction.
    """
    logger = logging.getLogger(__name__)
    mavsdk_server = None
    try:
        global HW_ID

        # Step 1: Start MAVSDK Server
        udp_port = Params.mavsdk_port
        mavsdk_server = start_mavsdk_server(udp_port)
        if mavsdk_server is None:
            logger.error("Failed to start MAVSDK server. Exiting program.")
            sys.exit(1)

        # Give the server time to spin up
        await asyncio.sleep(2)

        # Step 2: Initial Setup and Connection
        drone = await initial_setup_and_connection()

        # Step 3: Pre-flight Checks
        home_position = await pre_flight_checks(drone)

        # --- Capture precise launch position from telemetry ---
        launch_lat = launch_lon = launch_alt = None
        logger.info("Capturing launch position (lat/lon/alt) from telemetry...")
        start = time.time()
        async for pos in drone.telemetry.position():
            launch_lat = pos.latitude_deg
            launch_lon = pos.longitude_deg
            launch_alt = pos.absolute_altitude_m
            logger.info(
                f"Launch position captured: "
                f"lat={launch_lat:.6f}, lon={launch_lon:.6f}, alt={launch_alt:.2f}m"
            )
            break
        if launch_lat is None:
            logger.error("Failed to capture launch position from telemetry within timeout.")
            sys.exit(1)

        # Step 4: Handle synchronized start time
        if synchronized_start_time is None:
            synchronized_start_time = time.time()
            logger.info(f"No start_time provided; using now: {time.ctime(synchronized_start_time)}")
        now = time.time()
        if synchronized_start_time > now:
            wait_secs = synchronized_start_time - now
            logger.info(f"Waiting {wait_secs:.2f}s until synchronized start time.")
            await asyncio.sleep(wait_secs)
        elif synchronized_start_time < now:
            logger.warning(f"Start time was {now - synchronized_start_time:.2f}s ago; starting immediately.")
        else:
            logger.info("Synchronized start time is now.")

        # Step 5: Arm and enter Offboard
        await arming_and_starting_offboard_mode(drone, home_position)

        # Step 6: Load and adjust trajectory
        if custom_csv:
            trajectory_filename = os.path.join(
                'shapes_sitl' if Params.sim_mode else 'shapes',
                custom_csv
            )
            waypoints = read_trajectory_file(
                filename=trajectory_filename,
                auto_launch_position=auto_launch_position
            )
            logger.info(f"Loaded custom CSV '{custom_csv}'.")
        else:
            HW_ID = read_hw_id()
            if HW_ID is None:
                logger.error("Failed to read HW ID; exiting.")
                sys.exit(1)
            drone_config = read_config(CONFIG_CSV_NAME)
            if drone_config is None:
                logger.error("Drone config not found; exiting.")
                sys.exit(1)
            position_id = drone_config.pos_id
            trajectory_filename = os.path.join(
                'shapes_sitl' if Params.sim_mode else 'shapes',
                'swarm',
                'processed',
                f"Drone {position_id}.csv"
            )
            waypoints = read_trajectory_file(
                filename=trajectory_filename,
                auto_launch_position=auto_launch_position,
                initial_x=drone_config.initial_x,
                initial_y=drone_config.initial_y
            )
            logger.info(f"Loaded trajectory for Drone {position_id}.")

        # Step 7: Execute the show trajectory (now with global reference)
        await perform_trajectory(
            drone,
            waypoints,
            home_position,
            synchronized_start_time,
            launch_lat, launch_lon, launch_alt
        )

        logger.info("Mission completed successfully.")
        sys.exit(0)

    except Exception:
        logger.exception("Error running drone.")
        sys.exit(1)
    finally:
        if mavsdk_server:
            stop_mavsdk_server(mavsdk_server)




# ----------------------------- #
#             Main              #
# ----------------------------- #


def main():
    """
    Main function to run the drone.
    """
    # Configure logging
    configure_logging()
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description='Drone Show Script')
    parser.add_argument('--start_time', type=float, help='Synchronized start UNIX time')
    parser.add_argument('--custom_csv', type=str, help='Name of the custom trajectory CSV file, e.g., active.csv')
    parser.add_argument(
        '--auto_launch_position',
        type=str2bool,
        nargs='?',
        const=True,
        default=None,
        help='Explicitly enable (True) or disable (False) automated initial position extraction from trajectory CSV.',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode for verbose logging.',
    )
    args = parser.parse_args()

    # Adjust logging level based on debug flag
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug mode ENABLED: Verbose logging is active.")
    else:
        logging.getLogger().setLevel(logging.INFO)
        logger.info("Debug mode DISABLED: Standard logging level set to INFO.")

    # Get the synchronized start time
    if args.start_time:
        synchronized_start_time = args.start_time
        formatted_time = time.ctime(synchronized_start_time)
        logger.info(f"Synchronized start time provided: {formatted_time}.")
    else:
        synchronized_start_time = time.time()
        formatted_time = time.ctime(synchronized_start_time)
        logger.info(f"No synchronized start time provided. Using current time: {formatted_time}.")

    global global_synchronized_start_time
    global_synchronized_start_time = synchronized_start_time

    # Determine if auto launch position is enabled
    if args.auto_launch_position is not None:
        auto_launch_position = args.auto_launch_position
        logger.info(f"Command-line argument '--auto_launch_position' set to {auto_launch_position}.")
    else:
        auto_launch_position = Params.AUTO_LAUNCH_POSITION
        logger.info(
            f"Using Params.AUTO_LAUNCH_POSITION = {Params.AUTO_LAUNCH_POSITION} "
            f"as '--auto_launch_position' was not provided."
        )

    # Display initial position configuration
    if auto_launch_position:
        logger.info("Initial Position: Auto Launch Position is ENABLED.")
        logger.info("Waypoints will be re-centered so the first waypoint becomes (0,0,0) in NED.")
    else:
        logger.info("Initial Position: Auto Launch Position is DISABLED.")
        logger.info("Positions will be shifted by config's initial_x and initial_y in NED.")

    try:
        asyncio.run(
            run_drone(
                synchronized_start_time,
                custom_csv=args.custom_csv,
                auto_launch_position=auto_launch_position,
            )
        )
    except Exception:
        logger.exception("Unhandled exception in main.")
        sys.exit(1)


if __name__ == "__main__":
    main()