/**
 * Health Advisor - Debug Panel JavaScript
 */

// State
let lastEventId = -1;
let autoRefreshInterval = null;
let currentFilter = '';

// DOM Elements
const eventsContainer = document.getElementById('eventsContainer');
const noEvents = document.getElementById('noEvents');
const configGrid = document.getElementById('configGrid');
const eventFilter = document.getElementById('eventFilter');

// API Base URL
const API_BASE = '';

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    refreshEvents();
    startAutoRefresh();
});

// ============================================================================
// Configuration Display
// ============================================================================

async function loadConfig() {
    try {
        const response = await fetch(`${API_BASE}/api/debug/config`);
        const config = await response.json();
        displayConfig(config);
    } catch (error) {
        console.error('Error loading config:', error);
        configGrid.innerHTML = '<div class="config-item"><span class="config-label">Error loading config</span></div>';
    }
}

function displayConfig(config) {
    const items = [];

    // MCP settings
    items.push({
        label: 'MCP Transport',
        value: config.mcp?.transport || 'N/A',
    });
    items.push({
        label: 'MCP Server',
        value: `${config.mcp?.host}:${config.mcp?.port}`,
    });

    // Security settings
    items.push({
        label: 'Authentication',
        value: config.security?.authentication?.enabled ? 'Enabled' : 'Disabled',
        enabled: config.security?.authentication?.enabled,
    });
    items.push({
        label: 'Input Validation',
        value: config.security?.input_validation?.level || 'N/A',
    });
    items.push({
        label: 'Prompt Injection Protection',
        value: config.security?.prompt_injection_protection ? 'Enabled' : 'Disabled',
        enabled: config.security?.prompt_injection_protection,
    });
    items.push({
        label: 'Data Filtering',
        value: config.security?.data_filtering ? 'Enabled' : 'Disabled',
        enabled: config.security?.data_filtering,
    });

    // Logging settings
    items.push({
        label: 'Log Level',
        value: config.logging?.level || 'N/A',
    });

    configGrid.innerHTML = items.map(item => `
        <div class="config-item">
            <span class="config-label">${item.label}:</span>
            <span class="config-value ${item.enabled === true ? 'enabled' : ''} ${item.enabled === false ? 'disabled' : ''}">${item.value}</span>
        </div>
    `).join('');
}

// ============================================================================
// Events Management
// ============================================================================

async function refreshEvents() {
    try {
        const params = new URLSearchParams();
        if (currentFilter) {
            params.set('event_type', currentFilter);
        }
        params.set('limit', '100');

        const response = await fetch(`${API_BASE}/api/debug/events?${params}`);
        const data = await response.json();

        displayEvents(data.events);

        // Update last event ID for auto-refresh
        if (data.events.length > 0) {
            lastEventId = Math.max(...data.events.map(e => e.id));
        }
    } catch (error) {
        console.error('Error loading events:', error);
    }
}

async function clearEvents() {
    try {
        await fetch(`${API_BASE}/api/debug/events`, { method: 'DELETE' });
        lastEventId = -1;
        refreshEvents();
    } catch (error) {
        console.error('Error clearing events:', error);
    }
}

function filterEvents() {
    currentFilter = eventFilter.value;
    refreshEvents();
}

function displayEvents(events) {
    if (events.length === 0) {
        noEvents.style.display = 'block';
        // Remove any existing event items
        const existingEvents = eventsContainer.querySelectorAll('.event-item');
        existingEvents.forEach(el => el.remove());
        return;
    }

    noEvents.style.display = 'none';

    // Clear and rebuild (simple approach for now)
    const existingEvents = eventsContainer.querySelectorAll('.event-item');
    existingEvents.forEach(el => el.remove());

    events.forEach(event => {
        const eventEl = createEventElement(event);
        eventsContainer.appendChild(eventEl);
    });
}

function createEventElement(event) {
    const div = document.createElement('div');
    div.className = 'event-item';
    div.dataset.eventId = event.id;

    const timestamp = new Date(event.timestamp).toLocaleTimeString();

    div.innerHTML = `
        <div class="event-header" onclick="toggleEvent(${event.id})">
            <div class="event-type">
                <span class="event-badge ${event.type}">${event.type}</span>
                <span class="event-category">${event.category}</span>
            </div>
            <span class="event-timestamp">${timestamp}</span>
        </div>
        <div class="event-content">
            <pre>${formatEventData(event.data)}</pre>
        </div>
    `;

    return div;
}

function formatEventData(data) {
    try {
        // Pretty print JSON
        return JSON.stringify(data, null, 2)
            // Escape HTML in the JSON
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    } catch (error) {
        return String(data);
    }
}

function toggleEvent(eventId) {
    const eventEl = document.querySelector(`[data-event-id="${eventId}"]`);
    if (eventEl) {
        eventEl.classList.toggle('expanded');
    }
}

// ============================================================================
// Auto-refresh
// ============================================================================

function startAutoRefresh() {
    // Poll every 2 seconds
    autoRefreshInterval = setInterval(async () => {
        try {
            const params = new URLSearchParams();
            if (currentFilter) {
                params.set('event_type', currentFilter);
            }
            if (lastEventId >= 0) {
                params.set('since_id', lastEventId);
            }
            params.set('limit', '50');

            const response = await fetch(`${API_BASE}/api/debug/events?${params}`);
            const data = await response.json();

            if (data.events.length > 0) {
                // Prepend new events
                const newEvents = data.events.filter(e => e.id > lastEventId);
                if (newEvents.length > 0) {
                    noEvents.style.display = 'none';

                    newEvents.reverse().forEach(event => {
                        const eventEl = createEventElement(event);
                        const firstEvent = eventsContainer.querySelector('.event-item');
                        if (firstEvent) {
                            eventsContainer.insertBefore(eventEl, firstEvent);
                        } else {
                            eventsContainer.appendChild(eventEl);
                        }
                    });

                    lastEventId = Math.max(...newEvents.map(e => e.id));
                }
            }
        } catch (error) {
            // Silently fail on auto-refresh errors
            console.debug('Auto-refresh error:', error);
        }
    }, 2000);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    stopAutoRefresh();
});
