/**
 * Health Advisor - Main Application JavaScript
 */

// State
let currentPatient = null;
let conversationHistory = [];

// DOM Elements
const patientSelect = document.getElementById('patientSelect');
const patientName = document.getElementById('patientName');
const patientDob = document.getElementById('patientDob');
const patientMemberId = document.getElementById('patientMemberId');
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const loadingOverlay = document.getElementById('loadingOverlay');
const examplePrompts = document.getElementById('examplePrompts');

// API Base URL
const API_BASE = '';

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    loadPatients();
    checkHealth();
});

async function checkHealth() {
    try {
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
// Patient Management
// ============================================================================

async function loadPatients() {
    try {
        const response = await fetch(`${API_BASE}/api/patients`);

        if (!response.ok) {
            throw new Error('Failed to load patients');
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
    } catch (error) {
        console.error('Error loading patients:', error);
        patientSelect.innerHTML = '<option value="">Error loading patients</option>';
        addSystemMessage('Error: Could not load patient list. Please ensure MCP server is running.');
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
        const response = await fetch(`${API_BASE}/api/patients/${patientId}`);
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
        addSystemMessage('Error loading patient information.');
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
        const response = await fetch(`${API_BASE}/api/chat`, {
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
            throw new Error(error.detail || 'Chat request failed');
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
