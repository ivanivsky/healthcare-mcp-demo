/**
 * Settings Page JavaScript
 * Handles security control toggles, Firebase auth, and API interactions.
 */

// Firebase configuration (loaded from backend)
let firebaseConfig = null;
let currentUser = null;
let currentUserRole = null;
let securityConfig = null;
let isReadOnly = false;

// Boolean controls to track for posture summary (excludes system_prompt_level)
const BOOLEAN_CONTROLS = [
    'authentication_required',
    'authorization_required',
    'mcp_transport_auth_required',
    'mcp_auth_context_signing_required',
    'deterministic_error_responses'
];

// Human-readable control names for display
const CONTROL_NAMES = {
    'authentication_required': 'Authentication',
    'authorization_required': 'Authorization',
    'mcp_transport_auth_required': 'MCP Transport Auth',
    'mcp_auth_context_signing_required': 'Auth Context Signing',
    'deterministic_error_responses': 'Deterministic Errors'
};

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log('[Settings] Page loaded, initializing...');

    try {
        // Load Firebase config from backend
        const configResponse = await fetch('/api/config');
        if (!configResponse.ok) {
            throw new Error('Failed to load Firebase config');
        }
        const configData = await configResponse.json();

        if (configData.error) {
            console.error('[Settings] Firebase config error:', configData);
            showError('Firebase configuration error. Check backend logs.');
            return;
        }

        firebaseConfig = configData.firebase;

        // Initialize Firebase
        if (!firebase.apps.length) {
            firebase.initializeApp(firebaseConfig);
        }

        // Set up auth state listener
        firebase.auth().onAuthStateChanged(handleAuthStateChange);

    } catch (error) {
        console.error('[Settings] Initialization error:', error);
        showError('Failed to initialize. Check console for details.');
    }
});

// ============================================================================
// Auth State Management
// ============================================================================

async function handleAuthStateChange(user) {
    console.log('[Settings] Auth state changed:', user ? user.email : 'signed out');

    if (!user) {
        // Not signed in - show auth required message
        showAuthRequired();
        return;
    }

    currentUser = user;

    // Update user info display
    updateUserInfo(user);

    // Check user role via /api/whoami
    try {
        const token = await user.getIdToken();
        const whoamiResponse = await fetch('/api/whoami', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (whoamiResponse.status === 401) {
            showAuthRequired();
            return;
        }

        if (!whoamiResponse.ok) {
            throw new Error('Failed to get user info');
        }

        const whoamiData = await whoamiResponse.json();
        currentUserRole = whoamiData.role;

        console.log('[Settings] User role:', currentUserRole);

        if (currentUserRole !== 'admin') {
            // Non-admin users see read-only view
            isReadOnly = true;
            await loadSecurityConfig();
            showSettingsContent();
            showReadOnlyBanner();
            return;
        }

        // User is admin - load and display settings with full access
        isReadOnly = false;
        await loadSecurityConfig();
        showSettingsContent();

    } catch (error) {
        console.error('[Settings] Error checking user role:', error);
        showError('Failed to verify permissions. Please try again.');
    }
}

function updateUserInfo(user) {
    const userInfoEl = document.getElementById('userInfo');
    const currentUserEl = document.getElementById('currentUser');

    if (user && userInfoEl && currentUserEl) {
        currentUserEl.textContent = user.email;
        userInfoEl.style.display = 'flex';
    }
}

// ============================================================================
// View State Management
// ============================================================================

function showLoading() {
    document.getElementById('settingsLoading').style.display = 'flex';
    document.getElementById('authRequired').style.display = 'none';
    document.getElementById('settingsContent').style.display = 'none';
}

function showAuthRequired() {
    document.getElementById('settingsLoading').style.display = 'none';
    document.getElementById('authRequired').style.display = 'flex';
    document.getElementById('settingsContent').style.display = 'none';
}

function showSettingsContent() {
    document.getElementById('settingsLoading').style.display = 'none';
    document.getElementById('authRequired').style.display = 'none';
    document.getElementById('settingsContent').style.display = 'block';
}

function showReadOnlyBanner() {
    const banner = document.getElementById('readonlyBanner');
    if (banner) banner.style.display = 'flex';
}

function showError(message) {
    // For now, just log to console - could add a toast/modal later
    console.error('[Settings] Error:', message);
    alert(message);
}

// ============================================================================
// Security Config API
// ============================================================================

async function loadSecurityConfig() {
    console.log('[Settings] Loading security config...');

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch('/api/admin/security-config', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 401) {
            showAuthRequired();
            return;
        }

        // For non-admins, try the debug config endpoint which is public
        if (response.status === 403) {
            console.log('[Settings] Admin endpoint forbidden, trying debug config...');
            const debugResponse = await fetch('/api/debug/config');
            if (debugResponse.ok) {
                const debugData = await debugResponse.json();
                securityConfig = debugData.security || {};
                console.log('[Settings] Security config loaded from debug endpoint:', securityConfig);
                renderControls();
                updatePostureSummary();
                return;
            }
            showError('Unable to load security configuration.');
            return;
        }

        if (!response.ok) {
            throw new Error('Failed to load security config');
        }

        const data = await response.json();
        securityConfig = data.controls;

        console.log('[Settings] Security config loaded:', securityConfig);

        // Render controls with current values
        renderControls();
        updatePostureSummary();

    } catch (error) {
        console.error('[Settings] Error loading security config:', error);
        showError('Failed to load security configuration.');
    }
}

async function updateSecurityControl(controlName, value) {
    // Guard against read-only mode
    if (isReadOnly) {
        console.log('[Settings] Ignoring update in read-only mode');
        return;
    }

    console.log(`[Settings] Updating ${controlName} to ${value}`);

    const statusEl = document.getElementById(`status-${controlName}`);
    if (statusEl) {
        statusEl.textContent = 'Saving...';
        statusEl.className = 'control-status control-status-saving';
    }

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch('/api/admin/security-config', {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ [controlName]: value })
        });

        if (response.status === 401) {
            showAuthRequired();
            return;
        }

        if (response.status === 403) {
            // User lost admin access
            isReadOnly = true;
            showReadOnlyBanner();
            applyReadOnlyState();
            revertControl(controlName);
            if (statusEl) {
                statusEl.textContent = 'Access denied';
                statusEl.className = 'control-status control-status-error';
            }
            return;
        }

        if (!response.ok) {
            throw new Error('Failed to update security config');
        }

        const data = await response.json();
        securityConfig = data.controls;

        // Show success
        if (statusEl) {
            statusEl.textContent = 'Saved';
            statusEl.className = 'control-status control-status-success';
            setTimeout(() => {
                statusEl.textContent = '';
                statusEl.className = 'control-status';
            }, 2000);
        }

        // Update card styling and posture summary
        updateCardStyling(controlName, value);
        updatePostureSummary();

    } catch (error) {
        console.error(`[Settings] Error updating ${controlName}:`, error);

        // Revert toggle to previous state
        revertControl(controlName);

        if (statusEl) {
            statusEl.textContent = 'Failed';
            statusEl.className = 'control-status control-status-error';
            setTimeout(() => {
                statusEl.textContent = '';
                statusEl.className = 'control-status';
            }, 3000);
        }
    }
}

// ============================================================================
// UI Rendering
// ============================================================================

function renderControls() {
    // Set boolean toggle states
    for (const control of BOOLEAN_CONTROLS) {
        const toggle = document.getElementById(`toggle-${control}`);
        if (toggle && securityConfig[control] !== undefined) {
            toggle.checked = securityConfig[control];
            updateCardStyling(control, securityConfig[control]);
        }
    }

    // Set system_prompt_level radio
    const level = securityConfig.system_prompt_level || 'strong';
    const radioInputs = document.querySelectorAll('input[name="system_prompt_level"]');
    radioInputs.forEach(input => {
        input.checked = input.value === level;
    });
    updateCardStyling('system_prompt_level', level);

    // Set up event listeners for toggles
    for (const control of BOOLEAN_CONTROLS) {
        const toggle = document.getElementById(`toggle-${control}`);
        if (toggle) {
            toggle.addEventListener('change', (e) => {
                updateSecurityControl(control, e.target.checked);
            });
        }
    }

    // Set up event listener for system_prompt_level radios
    radioInputs.forEach(input => {
        input.addEventListener('change', (e) => {
            updateSecurityControl('system_prompt_level', e.target.value);
        });
    });

    // Apply read-only state if needed
    if (isReadOnly) {
        applyReadOnlyState();
    }
}

function applyReadOnlyState() {
    // Disable all toggle inputs
    document.querySelectorAll('.toggle-switch input').forEach(input => {
        input.disabled = true;
    });

    // Disable all radio inputs
    document.querySelectorAll('input[name="system_prompt_level"]').forEach(input => {
        input.disabled = true;
    });

    // Hide the reset button
    const resetBtn = document.getElementById('resetBtn');
    if (resetBtn) resetBtn.style.display = 'none';

    // Hide the user management section (admin-only)
    const userMgmtSection = document.getElementById('userManagementSection');
    if (userMgmtSection) userMgmtSection.style.display = 'none';

    // Add a visual class to the settings content
    document.getElementById('settingsContent').classList.add('settings-readonly');
}

function updateCardStyling(controlName, value) {
    const card = document.getElementById(`card-${controlName}`);
    if (!card) return;

    // Remove existing state classes
    card.classList.remove('control-card-enabled', 'control-card-disabled');

    if (controlName === 'system_prompt_level') {
        // For system prompt level, "strong" is enabled, others are various levels of disabled
        if (value === 'strong') {
            card.classList.add('control-card-enabled');
        } else {
            card.classList.add('control-card-disabled');
        }
    } else {
        // Boolean controls
        if (value) {
            card.classList.add('control-card-enabled');
        } else {
            card.classList.add('control-card-disabled');
        }
    }
}

function revertControl(controlName) {
    if (controlName === 'system_prompt_level') {
        const level = securityConfig.system_prompt_level || 'strong';
        const radioInputs = document.querySelectorAll('input[name="system_prompt_level"]');
        radioInputs.forEach(input => {
            input.checked = input.value === level;
        });
    } else {
        const toggle = document.getElementById(`toggle-${controlName}`);
        if (toggle) {
            toggle.checked = securityConfig[controlName];
        }
    }
}

function updatePostureSummary() {
    // Count enabled boolean controls
    let enabledCount = 0;
    let disabledControls = [];

    for (const control of BOOLEAN_CONTROLS) {
        if (securityConfig[control]) {
            enabledCount++;
        } else {
            disabledControls.push(CONTROL_NAMES[control] || control);
        }
    }

    const totalCount = BOOLEAN_CONTROLS.length;
    const disabledCount = totalCount - enabledCount;

    // Update count text
    const postureCount = document.getElementById('postureCount');
    if (postureCount) {
        postureCount.textContent = `${enabledCount} of ${totalCount} controls enabled`;
    }

    // Update disabled list
    const postureDisabledList = document.getElementById('postureDisabledList');
    const postureDetails = document.getElementById('postureDetails');
    if (postureDisabledList && postureDetails) {
        if (disabledControls.length > 0) {
            postureDisabledList.textContent = disabledControls.join(', ');
            postureDetails.style.display = 'block';
        } else {
            postureDetails.style.display = 'none';
        }
    }

    // Update posture indicator color
    const postureSummary = document.getElementById('postureSummary');
    if (postureSummary) {
        postureSummary.classList.remove('posture-green', 'posture-yellow', 'posture-red');

        if (disabledCount === 0) {
            postureSummary.classList.add('posture-green');
        } else if (disabledCount === 1) {
            postureSummary.classList.add('posture-yellow');
        } else {
            postureSummary.classList.add('posture-red');
        }
    }
}

// ============================================================================
// Actions
// ============================================================================

async function handleReset() {
    // Guard against read-only mode
    if (isReadOnly) {
        console.log('[Settings] Reset blocked in read-only mode');
        return;
    }

    if (!confirm('Reset all controls to defaults? This will revert to the values in config.yaml.')) {
        return;
    }

    console.log('[Settings] Resetting to defaults...');

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch('/api/admin/security-config/reset', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 401) {
            showAuthRequired();
            return;
        }

        if (response.status === 403) {
            // User lost admin access
            isReadOnly = true;
            showReadOnlyBanner();
            applyReadOnlyState();
            return;
        }

        if (!response.ok) {
            throw new Error('Failed to reset security config');
        }

        // Reload page to reflect reset state
        window.location.reload();

    } catch (error) {
        console.error('[Settings] Error resetting config:', error);
        showError('Failed to reset configuration.');
    }
}

function handleLogout() {
    firebase.auth().signOut().then(() => {
        window.location.href = '/';
    }).catch((error) => {
        console.error('[Settings] Sign out error:', error);
    });
}

// ============================================================================
// User Management
// ============================================================================

// Store the currently looked-up user
let currentLookupUser = null;

async function lookupUser() {
    const emailInput = document.getElementById('userSearchEmail');
    const statusEl = document.getElementById('userSearchStatus');
    const email = emailInput.value.trim();

    if (!email) {
        statusEl.textContent = 'Please enter an email address';
        statusEl.className = 'user-search-status user-search-error';
        return;
    }

    statusEl.textContent = 'Looking up...';
    statusEl.className = 'user-search-status user-search-loading';

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch(`/api/admin/users?email=${encodeURIComponent(email)}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 404) {
            statusEl.textContent = 'User not found';
            statusEl.className = 'user-search-status user-search-error';
            hideUserResult();
            return;
        }

        if (response.status === 403) {
            statusEl.textContent = 'Admin access required';
            statusEl.className = 'user-search-status user-search-error';
            hideUserResult();
            return;
        }

        if (!response.ok) {
            throw new Error('Failed to look up user');
        }

        const data = await response.json();
        currentLookupUser = data;

        statusEl.textContent = '';
        statusEl.className = 'user-search-status';

        renderUserResult(data);

    } catch (error) {
        console.error('[Settings] User lookup error:', error);
        statusEl.textContent = 'Lookup failed';
        statusEl.className = 'user-search-status user-search-error';
        hideUserResult();
    }
}

function renderUserResult(user) {
    const card = document.getElementById('userResultCard');
    card.style.display = 'block';

    document.getElementById('userResultEmail').textContent = user.email;
    document.getElementById('userResultUid').textContent = `UID: ${user.uid}`;

    const claims = user.claims || {};
    document.getElementById('userCurrentRole').textContent = claims.role || '(none)';
    document.getElementById('userCurrentPatients').textContent =
        (claims.patient_ids && claims.patient_ids.length > 0)
            ? claims.patient_ids.join(', ')
            : '(none)';

    // Pre-fill the role select
    const roleSelect = document.getElementById('userNewRole');
    if (claims.role) {
        roleSelect.value = claims.role;
    }

    // Pre-fill the patients input
    const patientsInput = document.getElementById('userNewPatients');
    if (claims.patient_ids && claims.patient_ids.length > 0) {
        patientsInput.value = claims.patient_ids.join(', ');
    } else {
        patientsInput.value = '';
    }

    // Clear any previous status
    document.getElementById('userResultStatus').textContent = '';
}

function hideUserResult() {
    document.getElementById('userResultCard').style.display = 'none';
    currentLookupUser = null;
}

async function updateUserRole() {
    if (!currentLookupUser) return;

    const roleSelect = document.getElementById('userNewRole');
    const statusEl = document.getElementById('userResultStatus');
    const newRole = roleSelect.value;

    statusEl.textContent = 'Updating role...';
    statusEl.className = 'user-result-status user-result-loading';

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch(`/api/admin/users/${currentLookupUser.uid}/role`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ role: newRole })
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail?.message || 'Failed to update role');
        }

        const data = await response.json();
        currentLookupUser.claims = data.claims;
        renderUserResult(currentLookupUser);

        statusEl.textContent = 'Role updated';
        statusEl.className = 'user-result-status user-result-success';
        setTimeout(() => {
            statusEl.textContent = '';
            statusEl.className = 'user-result-status';
        }, 3000);

    } catch (error) {
        console.error('[Settings] Update role error:', error);
        statusEl.textContent = error.message || 'Update failed';
        statusEl.className = 'user-result-status user-result-error';
    }
}

async function updateUserPatients() {
    if (!currentLookupUser) return;

    const patientsInput = document.getElementById('userNewPatients');
    const statusEl = document.getElementById('userResultStatus');
    const inputValue = patientsInput.value.trim();

    // Parse comma-separated patient IDs
    let patientIds = [];
    if (inputValue) {
        patientIds = inputValue.split(',')
            .map(s => parseInt(s.trim(), 10))
            .filter(n => !isNaN(n));
    }

    statusEl.textContent = 'Updating patient access...';
    statusEl.className = 'user-result-status user-result-loading';

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch(`/api/admin/users/${currentLookupUser.uid}/patients`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ patient_ids: patientIds })
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail?.message || 'Failed to update patient access');
        }

        const data = await response.json();
        currentLookupUser.claims = data.claims;
        renderUserResult(currentLookupUser);

        statusEl.textContent = 'Patient access updated';
        statusEl.className = 'user-result-status user-result-success';
        setTimeout(() => {
            statusEl.textContent = '';
            statusEl.className = 'user-result-status';
        }, 3000);

    } catch (error) {
        console.error('[Settings] Update patients error:', error);
        statusEl.textContent = error.message || 'Update failed';
        statusEl.className = 'user-result-status user-result-error';
    }
}

async function clearUserClaims() {
    if (!currentLookupUser) return;

    if (!confirm(`Clear all claims for ${currentLookupUser.email}? They will need to re-bootstrap on next sign-in.`)) {
        return;
    }

    const statusEl = document.getElementById('userResultStatus');

    statusEl.textContent = 'Clearing claims...';
    statusEl.className = 'user-result-status user-result-loading';

    try {
        const token = await currentUser.getIdToken();
        const response = await fetch(`/api/admin/users/${currentLookupUser.uid}/claims`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail?.message || 'Failed to clear claims');
        }

        const data = await response.json();
        currentLookupUser.claims = data.claims;
        renderUserResult(currentLookupUser);

        statusEl.textContent = 'Claims cleared';
        statusEl.className = 'user-result-status user-result-success';
        setTimeout(() => {
            statusEl.textContent = '';
            statusEl.className = 'user-result-status';
        }, 3000);

    } catch (error) {
        console.error('[Settings] Clear claims error:', error);
        statusEl.textContent = error.message || 'Clear failed';
        statusEl.className = 'user-result-status user-result-error';
    }
}

// Make handleLogout available globally for onclick
window.handleLogout = handleLogout;
window.handleReset = handleReset;
window.lookupUser = lookupUser;
window.updateUserRole = updateUserRole;
window.updateUserPatients = updateUserPatients;
window.clearUserClaims = clearUserClaims;
