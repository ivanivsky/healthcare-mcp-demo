/**
 * My Health Access - Main Application JavaScript
 * Firebase Authentication Only
 */

// State
let currentPatient = null;
let conversationHistory = [];
let currentUser = null;
let firebaseInitialized = false;

// DOM Elements
const loginContainer = document.getElementById('loginContainer');
const patientContext = document.getElementById('patientContext');
const chatContainer = document.getElementById('chatContainer');
const userInfo = document.getElementById('userInfo');
const currentUserDisplay = document.getElementById('currentUser');
const patientSelect = document.getElementById('patientSelect');
const patientName = document.getElementById('patientName');
const patientDob = document.getElementById('patientDob');
const patientMemberId = document.getElementById('patientMemberId');
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const loadingOverlay = document.getElementById('loadingOverlay');
const examplePrompts = document.getElementById('examplePrompts');
const loginError = document.getElementById('loginError');

// API Base URL
const API_BASE = '';

/**
 * Fetch with Firebase Authentication.
 * Gets a fresh ID token and adds Authorization header.
 */
async function fetchWithFirebaseAuth(url, options = {}) {
    const user = firebase.auth().currentUser;
    if (!user) {
        throw new Error('Not authenticated');
    }

    const token = await user.getIdToken();
    const headers = {
        ...options.headers,
        'Authorization': `Bearer ${token}`,
    };

    return fetch(url, {
        ...options,
        headers,
    });
}

// ============================================================================
// Initialization
// ============================================================================

/**
 * Load Firebase config from backend and initialize Firebase SDK.
 */
async function initializeFirebase() {
    try {
        const response = await fetch(`${API_BASE}/api/config`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail?.message || error.message || 'Failed to load config');
        }

        const config = await response.json();
        if (!config.firebase || !config.firebase.apiKey) {
            throw new Error('Invalid Firebase config received');
        }

        firebase.initializeApp(config.firebase);
        firebaseInitialized = true;
        console.log('Firebase initialized successfully');
        return true;
    } catch (error) {
        console.error('Failed to initialize Firebase:', error);
        showConfigError(error.message);
        return false;
    }
}

/**
 * Show configuration error on the login card.
 */
function showConfigError(message) {
    const loginError = document.getElementById('loginError');
    if (loginError) {
        loginError.textContent = `Configuration error: ${message}`;
        loginError.style.display = 'block';
    }
    // Disable the sign-in button
    const googleSignInBtn = document.getElementById('googleSignInBtn');
    if (googleSignInBtn) {
        googleSignInBtn.disabled = true;
        googleSignInBtn.style.opacity = '0.5';
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    // Load Firebase config and initialize
    const initialized = await initializeFirebase();
    if (!initialized) {
        // Show login UI with error state
        showLoginUI();
        return;
    }

    // Bind whoami button click handlers
    const whoamiBtn = document.getElementById('whoamiBtn');
    if (whoamiBtn) {
        whoamiBtn.addEventListener('click', (e) => {
            e.preventDefault();
            callWhoami();
        });
    }
    const whoamiClearBtn = document.getElementById('whoamiClearBtn');
    if (whoamiClearBtn) {
        whoamiClearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            clearWhoamiResult();
        });
    }

    // Bind access button click handlers
    const accessBtn = document.getElementById('accessBtn');
    if (accessBtn) {
        accessBtn.addEventListener('click', (e) => {
            e.preventDefault();
            callAccess();
        });
    }
    const accessClearBtn = document.getElementById('accessClearBtn');
    if (accessClearBtn) {
        accessClearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            clearAccessResult();
        });
    }

    // Listen for Firebase auth state changes (only after Firebase is initialized)
    firebase.auth().onAuthStateChanged(async (user) => {
        if (user) {
            // User is signed in with Firebase
            try {
                currentUser = user.email || user.uid;
                showAuthenticatedUI();
                showWhoamiSection();
                loadPatients();
                checkHealth();
            } catch (error) {
                console.error('Error during auth state change:', error);
                showLoginUI();
            }
        } else {
            // No Firebase user - show login
            currentUser = null;
            hideWhoamiSection();
            showLoginUI();
        }
    });
});

async function checkHealth() {
    try {
        // Health check is public, no auth needed
        const response = await fetch(`${API_BASE}/api/health`);
        const data = await response.json();

        if (!data.mcp_connected) {
            addSystemMessage('Warning: MCP server is not connected. Chat functionality may be limited.');
        }
    } catch (error) {
        addSystemMessage('Error: Could not connect to backend server. Please ensure it is running.');
    }
}

// ============================================================================
// Authentication (Firebase Only)
// ============================================================================

function showLoginUI() {
    loginContainer.style.display = 'flex';
    patientContext.style.display = 'none';
    chatContainer.style.display = 'none';
    userInfo.style.display = 'none';
}

function showAuthenticatedUI() {
    loginContainer.style.display = 'none';
    patientContext.style.display = 'flex';
    chatContainer.style.display = 'flex';
    userInfo.style.display = 'flex';
    currentUserDisplay.textContent = currentUser;
    patientSelect.disabled = false;
}

async function handleLogout() {
    // Sign out from Firebase
    try {
        await firebase.auth().signOut();
    } catch (error) {
        console.error('Firebase sign out error:', error);
    }

    // Reset state
    currentUser = null;
    currentPatient = null;
    conversationHistory = [];

    // Clear UI
    clearPatientInfo();
    clearMessages();

    // Hide whoami section
    hideWhoamiSection();

    // Show login
    showLoginUI();
}

function showLoginError(message) {
    loginError.textContent = message;
    loginError.style.display = 'block';
}

// ============================================================================
// Firebase Google Sign-In
// ============================================================================

async function handleGoogleSignIn() {
    const provider = new firebase.auth.GoogleAuthProvider();

    try {
        // Try popup first
        await firebase.auth().signInWithPopup(provider);
        // Auth state change will be handled by onAuthStateChanged
    } catch (error) {
        if (error.code === 'auth/popup-blocked') {
            // Fallback to redirect if popup is blocked
            console.log('Popup blocked, using redirect...');
            await firebase.auth().signInWithRedirect(provider);
        } else {
            console.error('Google Sign-In error:', error);
            showLoginError(error.message || 'Sign-in failed');
        }
    }
}

function showWhoamiSection() {
    const whoamiSection = document.getElementById('whoamiSection');
    const user = firebase.auth().currentUser;
    if (whoamiSection && user) {
        whoamiSection.style.display = 'block';
    }
}

function hideWhoamiSection() {
    const whoamiSection = document.getElementById('whoamiSection');
    if (whoamiSection) {
        whoamiSection.style.display = 'none';
    }
    // Clear any previous results
    const resultEl = document.getElementById('whoamiResult');
    if (resultEl) {
        resultEl.textContent = '';
    }
}

function clearWhoamiResult() {
    const resultEl = document.getElementById('whoamiResult');
    if (resultEl) {
        resultEl.textContent = '';
    }
}

async function callWhoami() {
    const resultEl = document.getElementById('whoamiResult');
    if (!resultEl) return;

    try {
        // Check if user is signed in
        const user = firebase.auth().currentUser;
        console.log("WHOAMI: user signed in?", !!user);

        if (!user) {
            resultEl.textContent = JSON.stringify({
                error: "NOT_SIGNED_IN",
                message: "No Firebase user is currently signed in."
            }, null, 2);
            return;
        }

        resultEl.textContent = 'Fetching token and calling /api/whoami...';

        // Fetch fresh ID token at call time
        const token = await user.getIdToken();
        console.log("WHOAMI: token fetched?", !!token);

        // Build URL explicitly as same-origin
        const url = `${window.location.origin}/api/whoami`;
        console.log("WHOAMI: calling URL", url);

        // Build headers with Authorization
        const headers = {
            'Authorization': `Bearer ${token}`,
            'Accept': 'application/json'
        };
        console.log("WHOAMI: headers keys", Object.keys(headers));

        const response = await fetch(url, {
            method: 'GET',
            headers: headers
        });

        const data = await response.json();
        resultEl.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
        console.error('Whoami error:', error);
        resultEl.textContent = JSON.stringify({
            error: "CALL_FAILED",
            message: error.message || "Unknown error occurred"
        }, null, 2);
    }
}

// ============================================================================
// /api/access Tester
// ============================================================================

async function callAccess() {
    const resultEl = document.getElementById('accessResult');
    if (!resultEl) return;

    try {
        const user = firebase.auth().currentUser;

        if (!user) {
            resultEl.textContent = JSON.stringify({
                error: "NOT_SIGNED_IN",
                message: "No Firebase user is currently signed in."
            }, null, 2);
            return;
        }

        resultEl.textContent = 'Fetching token and calling /api/access...';

        const token = await user.getIdToken();
        const url = `${window.location.origin}/api/access`;

        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Accept': 'application/json'
            }
        });

        const data = await response.json();
        resultEl.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
        console.error('Access error:', error);
        resultEl.textContent = JSON.stringify({
            error: "CALL_FAILED",
            message: error.message || "Unknown error occurred"
        }, null, 2);
    }
}

function clearAccessResult() {
    const resultEl = document.getElementById('accessResult');
    if (resultEl) {
        resultEl.textContent = '';
    }
}

// Attach to window for inline onclick handlers
window.callWhoami = callWhoami;
window.clearWhoamiResult = clearWhoamiResult;
window.callAccess = callAccess;
window.clearAccessResult = clearAccessResult;

// ============================================================================
// Patient Management
// ============================================================================

async function loadPatients() {
    try {
        const response = await fetchWithFirebaseAuth(`${API_BASE}/api/patients`);

        if (!response.ok) {
            const error = await response.json();
            // Handle 403 specifically
            if (response.status === 403) {
                throw new Error('You do not have access to any patients.');
            }
            throw new Error(error.detail?.message || error.detail || 'Failed to load patients');
        }

        const patients = await response.json();

        // Populate dropdown
        patientSelect.innerHTML = '<option value="">-- Select a patient --</option>';
        patients.forEach(patient => {
            const option = document.createElement('option');
            option.value = patient.id;
            option.textContent = `${patient.first_name} ${patient.last_name} (${patient.member_id})`;
            patientSelect.appendChild(option);
        });

        if (patients.length === 0) {
            addSystemMessage('No patients available. Use "Test /api/whoami" to get your UID, then grant patient access.');
        }
    } catch (error) {
        console.error('Error loading patients:', error);
        patientSelect.innerHTML = '<option value="">Error loading patients</option>';
        addSystemMessage(`Error: Could not load patient list. ${error.message}`);
    }
}

async function handlePatientChange() {
    const patientId = patientSelect.value;

    if (!patientId) {
        clearPatientInfo();
        disableChat();
        return;
    }

    try {
        const response = await fetchWithFirebaseAuth(`${API_BASE}/api/patients/${patientId}`);

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail?.message || error.detail || 'Failed to load patient');
        }

        const data = await response.json();

        if (data.patient) {
            currentPatient = data.patient;
            displayPatientInfo(data.patient);
            enableChat();

            // Clear conversation when switching patients
            conversationHistory = [];
            clearMessages();
            addSystemMessage(`You are now viewing records for ${data.patient.first_name} ${data.patient.last_name}. How can I help you today?`);
        }
    } catch (error) {
        console.error('Error loading patient:', error);
        addSystemMessage(`Error: ${error.message}`);
    }
}

function displayPatientInfo(patient) {
    patientName.textContent = `${patient.first_name} ${patient.last_name}`;
    patientDob.textContent = `DOB: ${formatDate(patient.date_of_birth)}`;
    patientMemberId.textContent = `Member ID: ${patient.member_id}`;
}

function clearPatientInfo() {
    currentPatient = null;
    patientName.textContent = '--';
    patientDob.textContent = 'DOB: --';
    patientMemberId.textContent = 'Member ID: --';
}

// ============================================================================
// Chat Functionality
// ============================================================================

function enableChat() {
    chatInput.disabled = false;
    sendBtn.disabled = false;
    chatInput.placeholder = 'Ask about your health information...';

    // Enable example prompt buttons
    document.querySelectorAll('.prompt-btn').forEach(btn => {
        btn.disabled = false;
    });
}

function disableChat() {
    chatInput.disabled = true;
    sendBtn.disabled = true;
    chatInput.placeholder = 'Select a patient to start chatting...';

    // Disable example prompt buttons
    document.querySelectorAll('.prompt-btn').forEach(btn => {
        btn.disabled = true;
    });
}

async function handleSubmit(event) {
    event.preventDefault();

    const message = chatInput.value.trim();
    if (!message || !currentPatient) return;

    await sendMessage(message);
}

async function sendExamplePrompt(prompt) {
    if (!currentPatient) return;
    await sendMessage(prompt);
}

async function sendMessage(message) {
    // Add user message to UI
    addMessage(message, 'user');
    chatInput.value = '';

    // Show loading
    showLoading(true);

    try {
        const response = await fetchWithFirebaseAuth(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                patient_id: currentPatient.id,
                patient_name: `${currentPatient.first_name} ${currentPatient.last_name}`,
                conversation_history: conversationHistory,
            }),
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail?.message || error.detail || 'Chat request failed');
        }

        const data = await response.json();

        // Add assistant response to UI
        addMessage(data.response, 'assistant', data.tool_calls);

        // Update conversation history
        conversationHistory.push({ role: 'user', content: message });
        conversationHistory.push({ role: 'assistant', content: data.response });

    } catch (error) {
        console.error('Chat error:', error);
        addMessage(`Sorry, I encountered an error: ${error.message}`, 'assistant');
    } finally {
        showLoading(false);
    }
}

// ============================================================================
// UI Helpers
// ============================================================================

function addMessage(content, role, toolCalls = []) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = formatMessageContent(content);
    messageDiv.appendChild(contentDiv);

    // Add tool calls indicator if any
    if (toolCalls && toolCalls.length > 0) {
        const toolsDiv = document.createElement('div');
        toolsDiv.className = 'tool-calls';
        toolsDiv.innerHTML = `<small>Tools used: ${toolCalls.map(t => t.tool).join(', ')}</small>`;
        messageDiv.appendChild(toolsDiv);
    }

    chatMessages.appendChild(messageDiv);
    scrollToBottom();
}

function addSystemMessage(content) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message system-message';
    messageDiv.innerHTML = `<p>${content}</p>`;
    chatMessages.appendChild(messageDiv);
    scrollToBottom();
}

function clearMessages() {
    chatMessages.innerHTML = '';
}

function formatMessageContent(content) {
    // Basic markdown-like formatting
    let formatted = content
        // Escape HTML
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        // Bold
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        // Line breaks
        .replace(/\n/g, '<br>')
        // Lists (simple)
        .replace(/^- (.+)$/gm, '<li>$1</li>');

    // Wrap consecutive list items
    formatted = formatted.replace(/(<li>.*<\/li>\s*)+/g, '<ul>$&</ul>');

    return formatted;
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showLoading(show) {
    if (show) {
        loadingOverlay.classList.add('active');
        chatInput.disabled = true;
        sendBtn.disabled = true;
    } else {
        loadingOverlay.classList.remove('active');
        if (currentPatient) {
            chatInput.disabled = false;
            sendBtn.disabled = false;
        }
    }
}

function formatDate(dateString) {
    if (!dateString) return '--';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
}
