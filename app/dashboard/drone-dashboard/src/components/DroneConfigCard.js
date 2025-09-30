import React, { useState, useEffect, memo } from 'react';
import PropTypes from 'prop-types';
import DroneGitStatus from './DroneGitStatus';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faEdit,
  faTrash,
  faSave,
  faTimes,
  faCircle,
  faExclamationTriangle,
  faTimesCircle,
  faExclamationCircle,
  faPlusCircle,
  faSignal,
  faCheckCircle,
  faQuestionCircle,
} from '@fortawesome/free-solid-svg-icons';
import '../styles/DroneConfigCard.css';

/**
 * Utility: Finds a drone (other than the current one) that already uses `targetPosId`.
 * Returns the matched drone object or null if none is found.
 */
function findDroneByPositionId(configData, targetPosId, excludeHwId) {
  return configData.find(
    (d) => d.pos_id === targetPosId && d.hw_id !== excludeHwId
  );
}

/**
 * Compare config, assigned, and auto-detected pos_ids to decide how to display them.
 */
function determinePositionIdStatus(configPosId, assignedPosId, autoPosId) {
  const configStr = configPosId ? String(configPosId) : '';
  const assignedStr = assignedPosId ? String(assignedPosId) : '';
  const autoStr = autoPosId ? String(autoPosId) : '';

  // Flag if auto is "0" => effectively no auto detection
  const noAutoDetection = autoStr === '0' || !autoStr;

  // Drone has no heartbeat if there's no assigned pos_id AND no auto pos_id
  // (i.e. both assignedStr and autoStr are empty).
  const noHeartbeatData = !assignedStr && !autoStr;

  // Check if all three match
  const allMatch =
    configStr &&
    assignedStr &&
    configStr === assignedStr &&
    assignedStr === autoStr;

  // 2 match (config=assigned), but auto detection not available or zero
  const configAssignedMatchNoAuto =
    configStr && assignedStr && configStr === assignedStr && noAutoDetection;

  // Any mismatch
  const anyMismatch =
    !allMatch &&
    !configAssignedMatchNoAuto &&
    !noHeartbeatData &&
    (configStr !== assignedStr || configStr !== autoStr || assignedStr !== autoStr);

  return {
    configStr,
    assignedStr,
    autoStr,
    noAutoDetection,
    noHeartbeatData,
    allMatch,
    configAssignedMatchNoAuto,
    anyMismatch,
  };
}

/**
 * Read-only view of a drone card: Shows the drone's config data and status.
 */
const DroneReadOnlyView = memo(function DroneReadOnlyView({
  drone,
  gitStatus,
  gcsGitStatus,
  isNew,
  ipMismatch,
  heartbeatStatus,
  heartbeatAgeSec,
  heartbeatIP,
  networkInfo,
  onEdit,
  onRemove,
  configPosId,
  assignedPosId,
  autoPosId,
  onAcceptConfigFromAuto,
  onAcceptConfigFromHb,
}) {
  // Decide how to interpret the various pos_id values
  const {
    configStr,
    assignedStr,
    autoStr,
    noAutoDetection,
    noHeartbeatData,
    allMatch,
    configAssignedMatchNoAuto,
    anyMismatch,
  } = determinePositionIdStatus(configPosId, assignedPosId, autoPosId);

  /**
   * Returns the correct heartbeat status icon based on `heartbeatStatus`.
   */
  const getHeartbeatIcon = () => {
    switch (heartbeatStatus) {
      case 'Online (Recent)':
        return (
          <FontAwesomeIcon
            icon={faCircle}
            className="status-icon online"
            title="Online (Recent): Drone is actively sending heartbeat"
            aria-label="Online (Recent)"
          />
        );
      case 'Stale (>20s)':
        return (
          <FontAwesomeIcon
            icon={faExclamationTriangle}
            className="status-icon stale"
            title="Stale (>20s): Heartbeat hasn't been received recently"
            aria-label="Stale (>20s)"
          />
        );
      case 'Offline (>60s)':
        return (
          <FontAwesomeIcon
            icon={faTimesCircle}
            className="status-icon offline"
            title="Offline (>60s): Drone hasn't sent heartbeat in a long time"
            aria-label="Offline (>60s)"
          />
        );
      default:
        // "No heartbeat"
        return (
          <FontAwesomeIcon
            icon={faCircle}
            className="status-icon no-heartbeat"
            title="No Heartbeat: Drone is not connected or not sending heartbeat"
            aria-label="No Heartbeat"
          />
        );
    }
  };

  /**
   * Wi-Fi icon based on a numeric `strength`.
   */
  const getWifiIcon = (strength) => {
    if (strength >= 80) {
      return (
        <FontAwesomeIcon
          icon={faSignal}
          className="wifi-icon strong"
          title="Strong Wi-Fi Signal"
          aria-label="Strong Wi-Fi Signal"
        />
      );
    }
    if (strength >= 50) {
      return (
        <FontAwesomeIcon
          icon={faSignal}
          className="wifi-icon medium"
          title="Medium Wi-Fi Signal"
          aria-label="Medium Wi-Fi Signal"
        />
      );
    }
    if (strength > 0) {
      return (
        <FontAwesomeIcon
          icon={faSignal}
          className="wifi-icon weak"
          title="Weak Wi-Fi Signal"
          aria-label="Weak Wi-Fi Signal"
        />
      );
    }
    return (
      <FontAwesomeIcon
        icon={faSignal}
        className="wifi-icon none"
        title="No Wi-Fi Signal"
        aria-label="No Wi-Fi Signal"
      />
    );
  };

  /**
   * Render the Position ID section with logic for no heartbeat, mismatch, etc.
   */
  const renderPositionIdInfo = () => {
    // 0) If there's no heartbeat data at all => just show config pos_id in a neutral status
    if (noHeartbeatData) {
      return (
        <div className="position-status match">
          <div className="position-values">
            <div className="position-value">Config: {configStr || 'N/A'}</div>
          </div>
          <small>No heartbeat data available yet</small>
        </div>
      );
    }

    // 1) ALL MATCH => single line, green check
    if (allMatch) {
      return (
        <div className="position-status match">
          <span>All Position IDs Match: {configStr}</span>
          <FontAwesomeIcon
            icon={faCheckCircle}
            className="status-icon all-good"
            title="All three match: Config, Assigned, and Auto-detected"
          />
          <div className="position-values">
            <div className="position-value">Config: {configStr}</div>
            <div className="position-value">Assigned: {assignedStr}</div>
            <div className="position-value">Auto: {autoStr}</div>
          </div>
        </div>
      );
    }

    // 2) config=assigned, but no auto detection => single line, with a yellow icon
    if (configAssignedMatchNoAuto) {
      return (
        <div className="position-status partial">
          <span>Config & Assigned Match: {configStr}</span>
          <FontAwesomeIcon
            icon={faExclamationTriangle}
            className="status-icon stale"
            title="No auto-detection available. Config & assigned match."
          />
          <div className="position-values">
            <div className="position-value">Config: {configStr}</div>
            <div className="position-value">Assigned: {assignedStr}</div>
          </div>
          <small>Auto-detection not available</small>
        </div>
      );
    }

    // 3) ANY mismatch => show each ID, highlight differences, show accept buttons
    if (anyMismatch) {
      return (
        <div className="position-status mismatch">
          <span>Position ID Mismatch Detected</span>
          <small>Please ensure synchronization before proceeding</small>
          <div className="position-values">
            <div className="position-value">Config: {configStr || 'N/A'}</div>
            <div className="position-value">Assigned (HB): {assignedStr || 'N/A'}</div>
            <div className="position-value">Auto-detected (HB): {autoStr || 'N/A'}</div>
          </div>

          <div className="accept-buttons">
            {/* If auto != config, show Accept from Auto */}
            {autoStr && autoStr !== '0' && autoStr !== configStr && (
              <button
                type="button"
                className="accept-button"
                onClick={() => onAcceptConfigFromAuto?.(autoStr)}
                title="Accept auto-detected position ID"
                aria-label="Accept auto-detected position ID"
              >
                <FontAwesomeIcon icon={faCheckCircle} />
                Accept Auto ({autoStr})
              </button>
            )}
            {/* If assigned != config, show Accept from HB assigned */}
            {assignedStr && assignedStr !== configStr && (
              <button
                type="button"
                className="accept-button accept-assigned-btn"
                onClick={() => onAcceptConfigFromHb?.(assignedStr)}
                title="Accept assigned (heartbeat) position ID"
                aria-label="Accept assigned (heartbeat) position ID"
              >
                <FontAwesomeIcon icon={faCheckCircle} />
                Accept Assigned ({assignedStr})
              </button>
            )}
          </div>

          {noAutoDetection && (
            <small>Auto-detection is not available</small>
          )}
        </div>
      );
    }

    // Fallback: show config if none of the above scenarios match
    return (
      <div className="position-status match">
        <span>Position ID: {configStr || 'N/A'}</span>
      </div>
    );
  };

  return (
    <>
      {isNew && (
        <div className="new-drone-badge" aria-label="Newly Detected Drone">
          <FontAwesomeIcon icon={faPlusCircle} /> Newly Detected
        </div>
      )}

      {/* Card Header */}
      <div className="drone-card-header">
        <div className="drone-id-section">
          <h3 className="drone-title">Drone {drone.hw_id}</h3>
          <div className="drone-subtitle">
            <span className="drone-hardware-id">Hardware ID: {drone.hw_id}</span>
            <span className="drone-position-id">Position ID: {drone.pos_id}</span>
          </div>
        </div>
        <div className="card-actions">
          <div className={`status-badge ${heartbeatStatus.toLowerCase().replace(/[^a-z]/g, '')}`}>
            {getHeartbeatIcon()}
            <span className="status-text">{heartbeatStatus}</span>
            {heartbeatAgeSec !== null && <span className="status-time">({heartbeatAgeSec}s)</span>}
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="drone-content">

        {/* Basic Information */}
        <div className="info-section">
          <div className="info-row">
            <span className="info-label">IP Address</span>
            <span className={`info-value ${ipMismatch ? 'mismatch' : ''}`}>
              {drone.ip}
              {ipMismatch && heartbeatIP && (
                <FontAwesomeIcon
                  icon={faExclamationCircle}
                  title={`IP Mismatch: Heartbeat IP = ${heartbeatIP}`}
                  aria-label={`IP Mismatch: Heartbeat IP = ${heartbeatIP}`}
                />
              )}
            </span>
          </div>

          <div className="info-row">
            <span className="info-label">MAVLink Port</span>
            <span className="info-value">{drone.mavlink_port}</span>
          </div>

          <div className="info-row">
            <span className="info-label">Debug Port</span>
            <span className="info-value">{drone.debug_port}</span>
          </div>

          <div className="info-row">
            <span className="info-label">GCS IP</span>
            <span className="info-value">{drone.gcs_ip}</span>
          </div>

          <div className="info-row">
            <span className="info-label">Launch Position</span>
            <span className="info-value">({drone.x}, {drone.y})</span>
          </div>
        </div>

        {/* Position ID Section */}
        <div className="position-section">
          <div className="position-header">Position ID Status</div>
          <div className="position-content">
            {renderPositionIdInfo()}
          </div>
        </div>

      {/* Network Information Section */}
      <div className="network-section">
        <div className="network-header">
          <FontAwesomeIcon icon={faSignal} />
          Network Information
        </div>
        <div className="network-content">
          {networkInfo ? (
            <>
              <div className="network-row">
                <span className="network-label">Wi-Fi Network:</span>
                <span className="network-value">
                  {networkInfo?.wifi?.ssid || 'N/A'}
                  <span className={`network-status ${networkInfo?.wifi?.ssid ? 'connected' : 'disconnected'}`}>
                    {networkInfo?.wifi?.ssid ? 'Connected' : 'Disconnected'}
                  </span>
                </span>
              </div>
              <div className="network-row">
                <span className="network-label">Signal:</span>
                <span className="network-value">
                  {networkInfo?.wifi?.signal_strength_percent ?? 'N/A'}%
                  {getWifiIcon(networkInfo?.wifi?.signal_strength_percent)}
                </span>
              </div>
              <div className="network-row">
                <span className="network-label">Ethernet:</span>
                <span className="network-value">
                  {networkInfo?.ethernet?.interface || 'N/A'}
                  <span className={`network-status ${networkInfo?.ethernet?.interface ? 'connected' : 'unknown'}`}>
                    {networkInfo?.ethernet?.interface ? 'Active' : 'Unknown'}
                  </span>
                </span>
              </div>
            </>
          ) : (
            <div className="network-row">
              <span className="network-label">Status:</span>
              <span className="network-value">
                Network data unavailable
                <span className="network-status unknown">Offline</span>
              </span>
            </div>
          )}
        </div>
      </div>

        {/* Git Info for this drone */}
        <DroneGitStatus
          gitStatus={gitStatus}
          gcsGitStatus={gcsGitStatus}
          droneName={`Drone ${drone.hw_id}`}
        />

      </div>

      {/* Edit / Remove action buttons */}
      <div className="button-group">
        <button
          className="action-button secondary"
          onClick={onEdit}
          title="Edit drone configuration"
          aria-label="Edit drone configuration"
        >
          <FontAwesomeIcon icon={faEdit} /> Edit
        </button>
        <button
          className="action-button danger"
          onClick={onRemove}
          title="Remove this drone"
          aria-label="Remove this drone"
        >
          <FontAwesomeIcon icon={faTrash} /> Remove
        </button>
      </div>
    </>
  );
});

/**
 * Edit form: Allows user to modify hardware ID, IP, pos_id, etc.
 */
const DroneEditForm = memo(function DroneEditForm({
  droneData,
  errors,
  ipMismatch,
  heartbeatIP,
  onFieldChange,
  onAcceptIp,
  onSave,
  onCancel,
  hwIdOptions,
  configData,
  setDroneData,
  assignedPosId,
  autoPosId,
  onAcceptPos,
  onAcceptPosAuto,
}) {
  const [showPosChangeDialog, setShowPosChangeDialog] = useState(false);
  const [pendingPosId, setPendingPosId] = useState(null);

  const [isCustomPosId, setIsCustomPosId] = useState(false);
  const [customPosId, setCustomPosId] = useState('');

  // For confirmation dialog
  const [oldX, setOldX] = useState(droneData.x);
  const [oldY, setOldY] = useState(droneData.y);
  const [newX, setNewX] = useState(droneData.x);
  const [newY, setNewY] = useState(droneData.y);

  const [originalPosId, setOriginalPosId] = useState(droneData.pos_id);

  // Gather all pos_ids from configData for the dropdown
  const allPosIds = Array.from(new Set(configData.map((d) => d.pos_id)));
  if (!allPosIds.includes(droneData.pos_id)) {
    allPosIds.push(droneData.pos_id);
  }
  allPosIds.sort((a, b) => parseInt(a, 10) - parseInt(b, 10));

  /**
   * Handler: user changes the pos_id from the dropdown
   * => possibly open a confirmation to copy x,y from an existing drone with that pos_id.
   */
  const handlePosSelectChange = (e) => {
    const chosenPos = e.target.value;
    if (chosenPos === droneData.pos_id) return; // No real change
    setPendingPosId(chosenPos);

    // If another drone in configData has this pos_id, copy x,y from that drone
    const matchedDrone = findDroneByPositionId(
      configData,
      chosenPos,
      droneData.hw_id
    );

    setOldX(droneData.x);
    setOldY(droneData.y);

    if (matchedDrone) {
      setNewX(matchedDrone.x);
      setNewY(matchedDrone.y);
    } else {
      setNewX(droneData.x);
      setNewY(droneData.y);
    }

    setShowPosChangeDialog(true);
  };

  /** Cancel pos_id change => revert to the old pos_id. */
  const handleCancelPosChange = () => {
    setShowPosChangeDialog(false);
    setPendingPosId(null);
    onFieldChange({ target: { name: 'pos_id', value: originalPosId } });
  };

  /** Confirm pos_id change => update local droneData with new pos_id & possibly new x,y. */
  const handleConfirmPosChange = () => {
    if (!pendingPosId) {
      setShowPosChangeDialog(false);
      return;
    }

    // Update pos_id in the form
    onFieldChange({ target: { name: 'pos_id', value: pendingPosId } });

    const matchedDrone = findDroneByPositionId(
      configData,
      pendingPosId,
      droneData.hw_id
    );
    if (matchedDrone) {
      onFieldChange({ target: { name: 'x', value: matchedDrone.x } });
      onFieldChange({ target: { name: 'y', value: matchedDrone.y } });
      setDroneData((prevData) => ({
        ...prevData,
        pos_id: pendingPosId,
        x: matchedDrone.x,
        y: matchedDrone.y,
      }));
    } else {
      // No matched drone => just update pos_id
      setDroneData((prevData) => ({
        ...prevData,
        pos_id: pendingPosId,
      }));
    }

    setOriginalPosId(pendingPosId);
    setShowPosChangeDialog(false);
    setPendingPosId(null);
  };

  /** Generic onChange for other fields. */
  const handleGenericChange = (e) => {
    onFieldChange(e);
  };

  return (
    <>
      {showPosChangeDialog && (
        <div className="confirmation-dialog-backdrop">
          <div className="confirmation-dialog" role="dialog" aria-modal="true">
            <h4>Confirm Position ID Change</h4>
            <p>
              You are changing Position ID from <strong>{originalPosId}</strong> to{' '}
              <strong>{pendingPosId}</strong>.
            </p>
            <p>
              <em>Old (x,y):</em> ({oldX}, {oldY})
              <br />
              <em>New (x,y):</em> ({newX}, {newY})
            </p>
            <p style={{ marginTop: '1rem' }}>Do you want to proceed?</p>
            <div className="dialog-buttons">
              <button className="confirm-button" onClick={handleConfirmPosChange}>
                Yes
              </button>
              <button className="cancel-button" onClick={handleCancelPosChange}>
                No
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="drone-edit-form">
        <div className="form-header">
          <h3>Edit Drone {droneData.hw_id}</h3>
        </div>

        {/* Form Section */}
        <div className="form-section">

        {/* Hardware ID Field */}
        <div className="form-field">
          <label className="form-label">Hardware ID</label>
          <select
            name="hw_id"
            value={droneData.hw_id}
            onChange={handleGenericChange}
            className="form-select"
            title="Select Hardware ID"
            aria-label="Select Hardware ID"
          >
            {hwIdOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
          {errors.hw_id && <span className="error-message">{errors.hw_id}</span>}
        </div>

        {/* IP + mismatch acceptance */}
        <div className="form-field">
          <label className="form-label">IP Address</label>
          <div className="input-with-icon">
            <input
              type="text"
              name="ip"
              value={droneData.ip}
              onChange={handleGenericChange}
              className="form-input"
              placeholder="Enter IP Address"
              aria-label="IP Address"
              style={ipMismatch ? { borderColor: 'var(--color-danger)' } : {}}
            />
            {ipMismatch && (
              <FontAwesomeIcon
                icon={faExclamationCircle}
                className="warning-icon"
                title={`IP mismatch: Heartbeat IP=${heartbeatIP}`}
                aria-label={`IP mismatch: Heartbeat IP=${heartbeatIP}`}
              />
            )}
          </div>
          {errors.ip && <span className="error-message">{errors.ip}</span>}
          {ipMismatch && heartbeatIP && (
            <div className="mismatch-message">
              IP mismatch with heartbeat: {heartbeatIP}
              <button
                type="button"
                className="action-button success"
                onClick={onAcceptIp}
                title="Accept heartbeat IP"
                aria-label="Accept heartbeat IP"
              >
                <FontAwesomeIcon icon={faCheckCircle} /> Accept
              </button>
            </div>
          )}
        </div>

        {/* MavLink Port */}
        <div className="form-field">
          <label className="form-label">MavLink Port</label>
          <input
            type="text"
            name="mavlink_port"
            value={droneData.mavlink_port}
            onChange={handleGenericChange}
            className="form-input"
            placeholder="Enter MavLink Port"
            aria-label="MavLink Port"
          />
          {errors.mavlink_port && (
            <span className="error-message">{errors.mavlink_port}</span>
          )}
        </div>

        {/* Debug Port */}
        <div className="form-field">
          <label className="form-label">Debug Port</label>
          <input
            type="text"
            name="debug_port"
            value={droneData.debug_port}
            onChange={handleGenericChange}
            className="form-input"
            placeholder="Enter Debug Port"
            aria-label="Debug Port"
          />
          {errors.debug_port && (
            <span className="error-message">{errors.debug_port}</span>
          )}
        </div>

        {/* GCS IP */}
        <div className="form-field">
          <label className="form-label">GCS IP</label>
          <input
            type="text"
            name="gcs_ip"
            value={droneData.gcs_ip}
            onChange={handleGenericChange}
            className="form-input"
            placeholder="Enter GCS IP"
            aria-label="GCS IP"
          />
          {errors.gcs_ip && <span className="error-message">{errors.gcs_ip}</span>}
        </div>

        {/* X, Y */}
        <div className="form-field">
          <label className="form-label">Initial X</label>
          <input
            type="text"
            name="x"
            value={droneData.x}
            onChange={handleGenericChange}
            className="form-input"
            placeholder="Enter X Coordinate"
            aria-label="X Coordinate"
          />
          {errors.x && <span className="error-message">{errors.x}</span>}
        </div>

        <div className="form-field">
          <label className="form-label">Initial Y</label>
          <input
            type="text"
            name="y"
            value={droneData.y}
            onChange={handleGenericChange}
            className="form-input"
            placeholder="Enter Y Coordinate"
            aria-label="Y Coordinate"
          />
          {errors.y && <span className="error-message">{errors.y}</span>}
        </div>

        {/* Position ID with toggle for custom vs. existing */}
        <div className="form-field">
          <label className="form-label">Position ID</label>
          <div className="input-with-icon">
            {isCustomPosId ? (
              // A text field for entering a brand-new pos_id
              <input
                type="text"
                name="pos_id"
                value={customPosId}
                className="form-input"
                placeholder="Enter new Position ID"
                onChange={(e) => {
                  const newPos = e.target.value;
                  setCustomPosId(newPos);
                  setDroneData((prev) => ({
                    ...prev,
                    pos_id: newPos,
                    x: 0,
                    y: 0,
                  }));
                }}
                aria-label="Custom Position ID"
              />
            ) : (
              // A dropdown listing all known pos_ids from config
              <select
                name="pos_id"
                value={droneData.pos_id}
                onChange={handlePosSelectChange}
                className="form-select"
                aria-label="Select Position ID"
              >
                {allPosIds.map((pid) => (
                  <option key={pid} value={pid}>
                    {pid}
                  </option>
                ))}
              </select>
            )}

            {/* Toggle to switch between dropdown and custom input */}
            <div className="toggle-container">
              <label className="switch">
                <input
                  type="checkbox"
                  checked={isCustomPosId}
                  onChange={() => {
                    setIsCustomPosId((prev) => !prev);
                    if (!isCustomPosId) setCustomPosId('');
                  }}
                />
                <span className="slider round"></span>
              </label>
              <span className="toggle-label">
                {isCustomPosId
                  ? 'Enter New Position ID'
                  : 'Select Existing Position ID'}
              </span>
            </div>
          </div>
          {errors.pos_id && <span className="error-message">{errors.pos_id}</span>}
        </div>

        {/* If assigned pos_id != config pos_id, allow accept */}
        {assignedPosId &&
          assignedPosId !== droneData.pos_id &&
          assignedPosId !== '0' && (
            <div className="mismatch-message">
              Heartbeat assigned pos_id ({assignedPosId}) differs from current config
              <button
                type="button"
                className="action-button success"
                onClick={onAcceptPos}
                title="Accept heartbeat assigned Pos ID"
                aria-label="Accept heartbeat assigned Pos ID"
              >
                <FontAwesomeIcon icon={faCheckCircle} /> Accept
              </button>
            </div>
          )}

        {/* If auto-detected pos_id != config pos_id, allow accept */}
        {autoPosId && autoPosId !== '0' && autoPosId !== droneData.pos_id && (
          <div className="mismatch-message">
            Auto-detected pos_id ({autoPosId}) differs from current config
            <button
              type="button"
              className="action-button success"
              onClick={onAcceptPosAuto}
              title="Accept auto-detected Pos ID"
              aria-label="Accept auto-detected Pos ID"
            >
              <FontAwesomeIcon icon={faCheckCircle} /> Accept Auto
            </button>
          </div>
        )}

        </div>

        {/* Save / Cancel buttons */}
        <div className="button-group">
          <button
            className="action-button success"
            onClick={onSave}
            title="Save changes"
            aria-label="Save changes"
          >
            <FontAwesomeIcon icon={faSave} /> Save
          </button>
          <button
            className="action-button secondary"
            onClick={onCancel}
            title="Cancel editing"
            aria-label="Cancel editing"
          >
            <FontAwesomeIcon icon={faTimes} /> Cancel
          </button>
        </div>

      </div>
    </>
  );
});

/**
 * Main DroneConfigCard component:
 * Decides between Read-Only or Edit mode, handles mismatch logic, etc.
 */
export default function DroneConfigCard({
  drone,
  gitStatus,
  gcsGitStatus,
  configData,
  availableHwIds,
  editingDroneId,
  setEditingDroneId,
  saveChanges,
  removeDrone,
  networkInfo,
  heartbeatData = null, // might be null or undefined
}) {
  const isEditing = editingDroneId === drone.hw_id;

  // Local state for the edit form
  const [droneData, setDroneData] = useState({ ...drone });
  const [errors, setErrors] = useState({});

  // Reset local form when toggling edit mode
  useEffect(() => {
    if (isEditing) {
      setDroneData({ ...drone });
      setErrors({});
    }
  }, [isEditing, drone]);

  // Safely handle heartbeat data
  const safeHb = heartbeatData || {};
  const timestampVal = safeHb.timestamp;
  const now = Date.now();
  const heartbeatAgeSec =
    typeof timestampVal === 'number'
      ? Math.floor((now - timestampVal) / 1000)
      : null;

  // Determine textual heartbeat status
  let heartbeatStatus = 'No heartbeat';
  if (heartbeatAgeSec !== null) {
    if (heartbeatAgeSec < 20) heartbeatStatus = 'Online (Recent)';
    else if (heartbeatAgeSec < 60) heartbeatStatus = 'Stale (>20s)';
    else heartbeatStatus = 'Offline (>60s)';
  }

  // Mismatch checks for IP
  const ipMismatch = typeof safeHb.ip === 'string' && safeHb.ip !== drone.ip;

  // Position IDs from config & heartbeat
  const configPosId = drone.pos_id; // from config
  const assignedPosId = safeHb.pos_id ? String(safeHb.pos_id) : ''; // heartbeat assigned
  const autoPosId = safeHb.detected_pos_id ? String(safeHb.detected_pos_id) : '';

  // Additional highlight if mismatch or newly detected
  const hasAnyMismatch = ipMismatch || drone.isNew;

  // Status class for visual distinction
  const getStatusClass = () => {
    if (hasAnyMismatch) return ' mismatch-drone';
    if (heartbeatStatus === 'Online (Recent)') return ' status-online';
    if (heartbeatStatus === 'Stale (>20s)') return ' status-stale';
    if (heartbeatStatus === 'Offline (>60s)') return ' status-offline';
    return ' status-unknown';
  };

  const cardExtraClass = getStatusClass();

  /**
   * Validate local fields, then call `saveChanges` if no errors.
   */
  const handleLocalSave = () => {
    const validationErrors = {};

    if (!droneData.hw_id) {
      validationErrors.hw_id = 'Hardware ID is required.';
    }
    if (!droneData.ip) {
      validationErrors.ip = 'IP Address is required.';
    }
    if (!droneData.mavlink_port) {
      validationErrors.mavlink_port = 'MavLink Port is required.';
    }
    if (!droneData.debug_port) {
      validationErrors.debug_port = 'Debug Port is required.';
    }
    if (!droneData.gcs_ip) {
      validationErrors.gcs_ip = 'GCS IP is required.';
    }
    // Basic numeric check for x, y
    if (droneData.x === undefined || isNaN(droneData.x)) {
      validationErrors.x = 'A valid numeric X coordinate is required.';
    }
    if (droneData.y === undefined || isNaN(droneData.y)) {
      validationErrors.y = 'A valid numeric Y coordinate is required.';
    }
    if (!droneData.pos_id) {
      validationErrors.pos_id = 'Position ID is required.';
    }

    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    // If no validation errors, call parent to handle final saving
    saveChanges(drone.hw_id, droneData);
  };

  return (
    <div className={`drone-config-card${cardExtraClass}`}>
      {isEditing ? (
        <DroneEditForm
          droneData={droneData}
          errors={errors}
          ipMismatch={ipMismatch}
          heartbeatIP={safeHb.ip}
          assignedPosId={assignedPosId}
          autoPosId={autoPosId}
          onFieldChange={(e) => {
            const { name, value } = e.target;
            setDroneData({ ...droneData, [name]: value });
          }}
          onAcceptIp={() => {
            if (safeHb.ip) {
              setDroneData({ ...droneData, ip: safeHb.ip });
            }
          }}
          onAcceptPos={() => {
            if (assignedPosId && assignedPosId !== '0') {
              setDroneData({
                ...droneData,
                pos_id: assignedPosId,
              });
            }
          }}
          onAcceptPosAuto={() => {
            if (autoPosId && autoPosId !== '0') {
              setDroneData({
                ...droneData,
                pos_id: autoPosId,
              });
            }
          }}
          onSave={handleLocalSave}
          onCancel={() => {
            setEditingDroneId(null);
            setDroneData({ ...drone });
            setErrors({});
          }}
          hwIdOptions={availableHwIds}
          configData={configData}
          setDroneData={setDroneData}
        />
      ) : (
        <DroneReadOnlyView
          drone={drone}
          gitStatus={gitStatus}
          gcsGitStatus={gcsGitStatus}
          isNew={drone.isNew}
          ipMismatch={ipMismatch}
          heartbeatStatus={heartbeatStatus}
          heartbeatAgeSec={heartbeatAgeSec}
          heartbeatIP={safeHb.ip}
          networkInfo={networkInfo}
          configPosId={configPosId}
          assignedPosId={assignedPosId}
          autoPosId={autoPosId}
          onEdit={() => setEditingDroneId(drone.hw_id)}
          onRemove={() => removeDrone(drone.hw_id)}
          onAcceptConfigFromAuto={(detectedValue) => {
            if (!detectedValue || detectedValue === '0') return;
            saveChanges(drone.hw_id, { ...drone, pos_id: detectedValue });
          }}
          onAcceptConfigFromHb={(hbValue) => {
            if (!hbValue || hbValue === '0') return;
            saveChanges(drone.hw_id, { ...drone, pos_id: hbValue });
          }}
        />
      )}
    </div>
  );
}

DroneConfigCard.propTypes = {
  /** The drone object from your config (or fetched data). */
  drone: PropTypes.object.isRequired,

  /** Git statuses if relevant to show in the UI. */
  gitStatus: PropTypes.object,
  gcsGitStatus: PropTypes.object,

  /** The entire configData array, to look up pos_id collisions. */
  configData: PropTypes.array.isRequired,
  availableHwIds: PropTypes.array.isRequired,

  /** If this card is currently in "editing" mode, it will show the edit form. */
  editingDroneId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  setEditingDroneId: PropTypes.func.isRequired,

  /** Callback to save the changes in parent state or server. */
  saveChanges: PropTypes.func.isRequired,

  /** Callback to remove the drone entirely. */
  removeDrone: PropTypes.func.isRequired,

  /** Optional: network info object, if available. */
  networkInfo: PropTypes.object,

  /**
   * Optional: heartbeat data object, e.g. {
   *   ip, pos_id, detected_pos_id, timestamp, ...
   * }
   */
  heartbeatData: PropTypes.any,
};
