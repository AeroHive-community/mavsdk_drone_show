import React, { useState } from 'react';
import PropTypes from 'prop-types';
import '../styles/DroneGitStatus.css';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCheckCircle,
  faExclamationCircle,
  faChevronDown,
  faChevronUp,
  faCopy,
} from '@fortawesome/free-solid-svg-icons';

const DroneGitStatus = ({ gitStatus, gcsGitStatus, droneName }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);

  if (!gitStatus) {
    return <div className="git-status git-loading">Git status not available.</div>;
  }

  const isInSync = gcsGitStatus ? gitStatus.commit === gcsGitStatus.commit : false;

  const handleCopyCommit = async () => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(gitStatus.commit);
        console.log('Copied to clipboard:', gitStatus.commit);
        setCopySuccess(true);
      } else {
        // Fallback for unsupported browsers
        const textarea = document.createElement('textarea');
        textarea.value = gitStatus.commit;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        console.log('Copied to clipboard using fallback:', gitStatus.commit);
        setCopySuccess(true);
      }
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      console.error('Could not copy text: ', err);
    }
  };

  const toggleDetails = () => {
    setIsExpanded(!isExpanded);
  };

  // Add null checks for gitStatus.commit
  const shortCommitHash = gitStatus.commit ? gitStatus.commit.slice(0, 7) : 'N/A';

  return (
    <div className={`git-status-card ${isInSync ? 'sync' : 'not-sync'}`}>
      <div className="git-status-header">
        <div className="status-indicator">
          {isInSync ? (
            <FontAwesomeIcon icon={faCheckCircle} className="status-icon online" title="In Sync" aria-label="In Sync" />
          ) : (
            <FontAwesomeIcon icon={faExclamationCircle} className="status-icon dirty" title="Not In Sync" aria-label="Not In Sync" />
          )}
        </div>
        <div className="git-basic-info">
          <span className="branch-name" title={`Branch: ${gitStatus.branch}`}>
            {gitStatus.branch}
          </span>
          <span
            className="commit-hash"
            onClick={handleCopyCommit}
            title="Click to copy full commit hash"
            aria-label="Commit hash, click to copy"
          >
            {/* Use the new shortCommitHash variable */}
            {shortCommitHash}
            <FontAwesomeIcon icon={faCopy} className="copy-icon" />
            {copySuccess && <span className="copy-tooltip">Copied!</span>}
          </span>
        </div>
        <div className="details-toggle">
          <button
            className="toggle-button"
            onClick={toggleDetails}
            aria-expanded={isExpanded}
            aria-controls={`git-details-${droneName}`}
            title="Toggle Details"
            aria-label="Toggle Details"
          >
            <FontAwesomeIcon icon={isExpanded ? faChevronUp : faChevronDown} />
          </button>
        </div>
      </div>
      {isExpanded && (
        <div id={`git-details-${droneName}`} className="git-status-details">
          <div className="detail-row">
            <span className="detail-label">Full Commit Hash:</span>
            <span className="detail-value">{gitStatus.commit}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Commit Message:</span>
            <span className="detail-value">{gitStatus.commit_message}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Commit Date:</span>
            <span className="detail-value">{new Date(gitStatus.commit_date).toLocaleString()}</span>
          </div>
          <div className="detail-row">
            <span className="detail-label">Author:</span>
            <span className="detail-value">
              {gitStatus.author_name} &lt;{gitStatus.author_email}&gt;
            </span>
          </div>
          {gitStatus.uncommitted_changes && gitStatus.uncommitted_changes.length > 0 && (
            <div className="detail-row">
              <span className="detail-label">Uncommitted Changes:</span>
              <ul className="changes-list">
                {gitStatus.uncommitted_changes.map((change, index) => (
                  <li key={index}>{change}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {!isInSync && <div className="git-warning">Git status is not in sync with GCS.</div>}
    </div>
  );
};

DroneGitStatus.propTypes = {
  gitStatus: PropTypes.shape({
    branch: PropTypes.string.isRequired,
    commit: PropTypes.string.isRequired,
    status: PropTypes.string.isRequired,
    commit_date: PropTypes.string.isRequired,
    commit_message: PropTypes.string.isRequired,
    author_name: PropTypes.string.isRequired,
    author_email: PropTypes.string.isRequired,
    uncommitted_changes: PropTypes.arrayOf(PropTypes.string),
  }),
  gcsGitStatus: PropTypes.shape({
    commit: PropTypes.string.isRequired,
  }),
  droneName: PropTypes.string.isRequired,
};

export default DroneGitStatus;