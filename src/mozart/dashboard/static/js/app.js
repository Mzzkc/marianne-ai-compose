/**
 * Mozart Dashboard - Main JavaScript Application
 *
 * This file provides the Alpine.js stores and global utilities for the dashboard.
 * Uses Alpine.js for reactive state management and HTMX for HTTP interactions.
 */

// Global Alpine.js store for application state
document.addEventListener('alpine:init', () => {
    // Main application store
    Alpine.store('app', {
        // Core application state
        isDarkMode: localStorage.getItem('darkMode') === 'true' ||
                   (!localStorage.getItem('darkMode') && window.matchMedia('(prefers-color-scheme: dark)').matches),

        connectionStatus: 'disconnected', // 'connected', 'connecting', 'disconnected'
        _connectionLostShown: false, // show banner only after initial connection succeeds then drops
        _hadConnection: false,
        isRefreshing: false,
        mobileMenuOpen: false,

        // Dashboard statistics
        stats: {
            totalJobs: 0,
            runningJobs: 0,
            completedJobs: 0,
            failedJobs: 0
        },

        // Job data
        recentJobs: [],
        jobCount: 0,
        lastUpdate: 'Never',

        // Notification system
        notifications: [],
        notificationCounter: 0,

        // Modal system
        modal: {
            isOpen: false,
            title: '',
            content: '',
            htmlContent: false,
            htmxTarget: false,
            type: 'info', // 'info', 'success', 'warning', 'error'
            size: 'md', // 'sm', 'md', 'lg', 'xl', '2xl', '4xl'
            icon: true,
            buttons: []
        },

        // Enhanced error dialog system
        errorDialog: {
            show: false,
            title: '',
            message: '',
            code: null,
            status: null,
            statusText: '',
            details: null,
            suggestions: [],
            retryable: false,
            retryAction: null
        },

        // Initialize the application
        init() {
            this.applyDarkMode();
            this.setupEventListeners();
            this.setupHtmxListeners();
            this.loadInitialData();
            this.updateTimestamp();

            // Update timestamp every minute
            setInterval(() => this.updateTimestamp(), 60000);
        },

        // Dark mode management
        toggleDarkMode() {
            this.isDarkMode = !this.isDarkMode;
            localStorage.setItem('darkMode', this.isDarkMode.toString());
            this.applyDarkMode();
        },

        applyDarkMode() {
            if (this.isDarkMode) {
                document.documentElement.classList.add('dark');
            } else {
                document.documentElement.classList.remove('dark');
            }
        },

        // Connection status management
        setConnectionStatus(status) {
            const prev = this.connectionStatus;
            this.connectionStatus = status;

            if (status === 'connected') {
                this._hadConnection = true;
                this._connectionLostShown = false;
                if (prev === 'disconnected' && prev !== status) {
                    this.addNotification('Connected', 'Live updates enabled', 'success', 3000);
                }
            } else if (status === 'disconnected') {
                if (this._hadConnection) {
                    this._connectionLostShown = true;
                }
                if (prev === 'connected') {
                    this.addNotification('Disconnected', 'Live updates disabled', 'warning', 5000);
                }
            }
        },

        // Notification system
        addNotification(title, message = '', type = 'info', duration = 5000) {
            const id = ++this.notificationCounter;
            const notification = {
                id,
                title,
                message,
                type,
                visible: true
            };

            this.notifications.push(notification);

            // Auto-remove after duration
            if (duration > 0) {
                setTimeout(() => this.removeNotification(id), duration);
            }

            return id;
        },

        removeNotification(id) {
            const index = this.notifications.findIndex(n => n.id === id);
            if (index !== -1) {
                // Fade out animation
                this.notifications[index].visible = false;

                // Remove from array after animation
                setTimeout(() => {
                    this.notifications.splice(index, 1);
                }, 300);
            }
        },

        clearNotifications() {
            this.notifications.forEach(n => n.visible = false);
            setTimeout(() => {
                this.notifications.length = 0;
            }, 300);
        },

        // Modal system
        openModal(options = {}) {
            this.modal = {
                isOpen: true,
                title: options.title || '',
                content: options.content || '',
                htmlContent: options.htmlContent || false,
                htmxTarget: options.htmxTarget || false,
                type: options.type || 'info',
                size: options.size || 'md',
                icon: options.icon !== false,
                buttons: options.buttons || [
                    { id: 'close', text: 'Close', variant: 'secondary', action: 'close' }
                ]
            };
        },

        closeModal() {
            this.modal.isOpen = false;
        },

        handleModalButton(button) {
            if (button.action === 'close') {
                this.closeModal();
            } else if (button.action === 'callback' && button.callback) {
                button.callback();
            } else if (button.action === 'htmx' && button.hxGet) {
                // Trigger HTMX request
                htmx.ajax('GET', button.hxGet, {
                    target: button.target || '#modal-htmx-content',
                    swap: button.swap || 'innerHTML'
                });
            }
        },

        // Enhanced error dialog methods
        showErrorDialog(options = {}) {
            this.errorDialog = {
                show: true,
                title: options.title || 'Error',
                message: options.message || 'An unexpected error occurred.',
                code: options.code || null,
                status: options.status || null,
                statusText: options.statusText || '',
                details: options.details || null,
                suggestions: options.suggestions || [],
                retryable: options.retryable || false,
                retryAction: options.retryAction || null
            };
        },

        hideErrorDialog() {
            this.errorDialog.show = false;
        },

        copyErrorToClipboard() {
            const errorInfo = {
                title: this.errorDialog.title,
                message: this.errorDialog.message,
                code: this.errorDialog.code,
                status: this.errorDialog.status,
                statusText: this.errorDialog.statusText,
                details: this.errorDialog.details,
                timestamp: new Date().toISOString(),
                userAgent: navigator.userAgent,
                url: window.location.href
            };

            const errorText = `Mozart Dashboard Error Report
===========================================
Title: ${errorInfo.title}
Message: ${errorInfo.message}
${errorInfo.code ? `Code: ${errorInfo.code}` : ''}
${errorInfo.status ? `HTTP Status: ${errorInfo.status} ${errorInfo.statusText}` : ''}
Timestamp: ${errorInfo.timestamp}
URL: ${errorInfo.url}
User Agent: ${errorInfo.userAgent}
${errorInfo.details ? `\nDetails:\n${errorInfo.details}` : ''}`;

            navigator.clipboard.writeText(errorText).then(() => {
                this.addNotification('Copied', 'Error details copied to clipboard', 'info', 3000);
            }).catch(() => {
                // Fallback for older browsers
                const textArea = document.createElement('textarea');
                textArea.value = errorText;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                this.addNotification('Copied', 'Error details copied to clipboard', 'info', 3000);
            });
        },

        // Handle different types of errors with appropriate dialogs
        handleApiError(error, context = {}) {
            let suggestions = [];
            let retryable = false;

            // Determine error type and suggestions
            if (error.status) {
                switch (error.status) {
                    case 400:
                        suggestions = [
                            'Check your input data for correctness',
                            'Verify required fields are filled',
                            'Ensure data formats match requirements'
                        ];
                        break;
                    case 401:
                        suggestions = [
                            'Check if you are properly logged in',
                            'Refresh the page to renew your session'
                        ];
                        break;
                    case 403:
                        suggestions = [
                            'Contact your administrator for access',
                            'Verify you have the required permissions'
                        ];
                        break;
                    case 404:
                        suggestions = [
                            'Verify the resource still exists',
                            'Check if the URL is correct',
                            'Try navigating from the main dashboard'
                        ];
                        break;
                    case 409:
                        suggestions = [
                            'Wait a moment and try again',
                            'Check if another operation is in progress',
                            'Refresh the page to see current state'
                        ];
                        retryable = true;
                        break;
                    case 429:
                        suggestions = [
                            'Wait a moment before trying again',
                            'Reduce the frequency of requests'
                        ];
                        retryable = true;
                        break;
                    case 500:
                    case 502:
                    case 503:
                    case 504:
                        suggestions = [
                            'This appears to be a temporary server issue',
                            'Try again in a few moments',
                            'Contact support if the problem persists'
                        ];
                        retryable = true;
                        break;
                }
            }

            this.showErrorDialog({
                title: `${error.status ? `HTTP ${error.status}` : 'Network'} Error`,
                message: error.message || error.statusText || 'An unexpected error occurred',
                code: error.code || null,
                status: error.status || null,
                statusText: error.statusText || '',
                details: error.details || (error.response ? JSON.stringify(error.response, null, 2) : null),
                suggestions,
                retryable,
                retryAction: context.retryAction || null
            });
        },

        // Convenience methods for common modals
        openSettingsModal() {
            this.openModal({
                title: 'Dashboard Settings',
                content: 'Settings panel coming soon.',
                size: 'sm',
                type: 'info',
                buttons: [
                    { id: 'close', text: 'Close', variant: 'secondary', action: 'close' }
                ]
            });
        },

        showJobDetails(jobId) {
            this.openModal({
                title: 'Job Details',
                htmxTarget: true,
                size: '2xl',
                type: 'info',
                buttons: [
                    { id: 'close', text: 'Close', variant: 'secondary', action: 'close' }
                ]
            });

            // Load job details via HTMX
            htmx.ajax('GET', `/api/jobs/${jobId}`, {
                target: '#modal-htmx-content',
                swap: 'innerHTML'
            });
        },

        showApiStatus() {
            this.openModal({
                title: 'API Status',
                htmxTarget: true,
                size: 'lg',
                type: 'info',
                buttons: [
                    { id: 'close', text: 'Close', variant: 'secondary', action: 'close' }
                ]
            });

            // Load API status via HTMX
            htmx.ajax('GET', '/health', {
                target: '#modal-htmx-content',
                swap: 'innerHTML'
            });
        },

        // Data management
        loadInitialData() {
            this.updateTimestamp();
        },

        async refreshData() {
            // Individual pages handle their own data loading via htmx partials.
            // This method updates the shared timestamp and can be extended when
            // dashboard-level stats/recent-jobs endpoints are implemented.
            this.updateTimestamp();
        },

        updateStats(data) {
            this.stats = { ...this.stats, ...data };
            this.jobCount = this.stats.totalJobs;
        },

        updateRecentJobs(jobs) {
            this.recentJobs = jobs || [];
        },

        updateTimestamp() {
            this.lastUpdate = new Date().toLocaleTimeString();
        },

        // Utility methods
        formatTimestamp(timestamp) {
            if (!timestamp) return 'Unknown';

            const date = new Date(timestamp);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / (1000 * 60));
            const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}m ago`;
            if (diffHours < 24) return `${diffHours}h ago`;
            if (diffDays < 7) return `${diffDays}d ago`;

            return date.toLocaleDateString();
        },

        // Event listeners
        setupEventListeners() {
            // Handle keyboard shortcuts
            document.addEventListener('keydown', (e) => {
                // Escape key closes modal
                if (e.key === 'Escape' && this.modal.isOpen) {
                    this.closeModal();
                }

                // Ctrl/Cmd + R refreshes data
                if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
                    e.preventDefault();
                    this.refreshData();
                }
            });

            // Handle system dark mode changes
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
                if (!localStorage.getItem('darkMode')) {
                    this.isDarkMode = e.matches;
                    this.applyDarkMode();
                }
            });

            // Handle window focus (refresh data when returning to tab)
            window.addEventListener('focus', () => {
                if (document.visibilityState === 'visible') {
                    this.refreshData();
                }
            });
        },

        setupHtmxListeners() {
            // Handle HTMX events for notifications and status updates
            document.addEventListener('htmx:responseError', (e) => {
                const xhr = e.detail.xhr;

                // Parse error response if available
                let errorMessage = xhr.statusText;
                let errorDetails = null;

                try {
                    const response = JSON.parse(xhr.responseText);
                    if (response.detail) {
                        errorMessage = response.detail;
                    }
                    errorDetails = xhr.responseText;
                } catch (parseError) {
                    errorDetails = xhr.responseText;
                }

                // Show enhanced error dialog for important errors
                if (xhr.status >= 400 && xhr.status < 600) {
                    this.handleApiError({
                        status: xhr.status,
                        statusText: xhr.statusText,
                        message: errorMessage,
                        details: errorDetails,
                        response: xhr.responseText
                    }, {
                        retryAction: () => {
                            // Try to re-trigger the original request
                            if (e.detail.elt) {
                                htmx.trigger(e.detail.elt, e.detail.requestConfig.verb.toLowerCase());
                            }
                        }
                    });
                } else {
                    // Fallback to notification for lesser errors
                    this.addNotification(
                        'Request Failed',
                        `HTTP ${xhr.status}: ${errorMessage}`,
                        'error',
                        5000
                    );
                }
            });

            document.addEventListener('htmx:sendError', (e) => {
                this.addNotification(
                    'Network Error',
                    'Unable to reach the server',
                    'error',
                    5000
                );
                this.setConnectionStatus('disconnected');
            });

            // Handle htmx request timeouts
            document.addEventListener('htmx:timeout', (e) => {
                this.addNotification(
                    'Request Timeout',
                    'The server took too long to respond. Try again.',
                    'warning',
                    6000
                );
            });

            // Intercept non-2xx htmx swaps to render friendly inline errors
            // instead of raw JSON or blank content
            document.addEventListener('htmx:beforeSwap', (e) => {
                const status = e.detail.xhr.status;
                if (status >= 400) {
                    // Allow the swap but replace content with a user-friendly error
                    e.detail.shouldSwap = true;
                    e.detail.isError = false;

                    let message = 'Something went wrong.';
                    let hint = '';
                    if (status === 404) {
                        message = 'The requested resource was not found.';
                        hint = 'It may have been removed or the URL is incorrect.';
                    } else if (status === 502 || status === 503) {
                        message = 'The server is temporarily unavailable.';
                        hint = 'The conductor may not be running. Start it with <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">mozart start</code>.';
                    } else if (status === 500) {
                        message = 'An internal server error occurred.';
                        hint = 'Check the server logs for details.';
                    } else if (status === 429) {
                        message = 'Too many requests. Please slow down.';
                        hint = 'Wait a moment and try again.';
                    }

                    e.detail.serverResponse = `
                        <div class="text-center py-8">
                            <svg class="mx-auto h-10 w-10 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>
                            </svg>
                            <h3 class="mt-3 text-sm font-medium text-gray-900 dark:text-white">Error ${status}</h3>
                            <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">${message}</p>
                            ${hint ? '<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">' + hint + '</p>' : ''}
                        </div>`;
                }
            });

            // Handle SSE connection status (not regular htmx requests)
            document.addEventListener('htmx:sseError', () => {
                this.setConnectionStatus('disconnected');
            });

            document.addEventListener('htmx:sseOpen', () => {
                this.setConnectionStatus('connected');
            });

            // Custom event handlers for data updates
            document.addEventListener('dashboard:stats-update', (e) => {
                if (e.detail) {
                    this.updateStats(e.detail);
                }
            });

            document.addEventListener('dashboard:jobs-update', (e) => {
                if (e.detail) {
                    this.updateRecentJobs(e.detail);
                }
            });
        }
    });
});

// Make the app store available globally for x-data binding
function mozartApp() {
    return Alpine.store('app');
}

// Global utility functions
window.MozartDashboard = {
    // API helpers
    async get(url) {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
    },

    async post(url, data = {}) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
    },

    // Notification helpers
    notify: {
        success(title, message = '') {
            Alpine.store('app').addNotification(title, message, 'success', 5000);
        },

        error(title, message = '') {
            Alpine.store('app').addNotification(title, message, 'error', 8000);
        },

        warning(title, message = '') {
            Alpine.store('app').addNotification(title, message, 'warning', 6000);
        },

        info(title, message = '') {
            Alpine.store('app').addNotification(title, message, 'info', 4000);
        }
    },

    // Formatting helpers
    format: {
        fileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        },

        duration(seconds) {
            if (!seconds) return '0s';
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);

            if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
            if (minutes > 0) return `${minutes}m ${secs}s`;
            return `${secs}s`;
        }
    }
};

// Alpine.js Score Editor Component
window.scoreEditor = function() {
    return {
        // Core editor state
        editor: null,
        editorMode: 'yaml',
        isFullscreen: false,
        isValidating: false,

        // Editor content and statistics
        content: '',
        currentLine: 1,
        currentColumn: 1,
        lineCount: 1,
        characterCount: 0,

        // Validation state
        validationStatus: 'unknown', // 'valid', 'invalid', 'warning', 'unknown'
        validationMessage: 'Ready',
        errors: [],
        errorCount: 0,
        showErrors: false,
        configSummary: null,

        // Submit state
        isSubmitting: false,
        showSubmitModal: false,
        currentFileName: 'mozart-score.yaml',

        // CodeMirror 6 decoration state (stored externally for simple updates)
        _decorationCompartment: null,
        _currentDecorations: [],

        // Template management
        showTemplateModal: false,
        templates: [
            {
                id: 'basic',
                name: 'Basic Job',
                description: 'Simple single-sheet job configuration',
                content: `# Basic Mozart Job Configuration
name: "basic-job"
description: "A simple Mozart AI job"

backend:
  type: "claude_cli"

sheets:
  - name: "main-task"
    description: "Main task execution"
    command: "echo 'Hello Mozart'"

timeout_seconds: 300
workspace: "./workspace"
`
            },
            {
                id: 'multi-sheet',
                name: 'Multi-Sheet Job',
                description: 'Job with multiple sequential sheets',
                content: `# Multi-Sheet Mozart Job Configuration
name: "multi-sheet-job"
description: "A Mozart job with multiple execution phases"

backend:
  type: "claude_cli"

sheets:
  - name: "setup"
    description: "Environment setup"
    command: "mkdir -p output && echo 'Setup complete'"

  - name: "main-work"
    description: "Main processing task"
    command: "echo 'Processing data' > output/result.txt"
    dependencies: ["setup"]

  - name: "cleanup"
    description: "Cleanup and finalization"
    command: "ls -la output/ && echo 'Job complete'"
    dependencies: ["main-work"]

timeout_seconds: 600
workspace: "./workspace"
`
            },
            {
                id: 'validation',
                name: 'Job with Validation',
                description: 'Job including output validation rules',
                content: `# Mozart Job with Validation
name: "validated-job"
description: "A job with comprehensive validation"

backend:
  type: "claude_cli"

sheets:
  - name: "task"
    description: "Main task with validation"
    command: "echo 'SUCCESS: Task completed' && echo 'Output data' > result.txt"
    validation:
      - type: "file_exists"
        path: "result.txt"
        description: "Output file must exist"
      - type: "content_contains"
        path: "result.txt"
        pattern: "Output data"
        description: "File must contain expected content"

timeout_seconds: 300
workspace: "./workspace"
retry_count: 3
`
            }
        ],

        // Initialize the component
        init() {
            this.$nextTick(() => {
                this.setupEditor();
                this.setupEventListeners();
                // Expose instance for page-level integration (save button, template loading)
                window.scoreEditorInstance = this;
            });
        },

        // Load template content from external source (used by page-level script)
        loadTemplateContent(content, title) {
            this.setContent(content);
            if (title) this.currentFileName = title.replace(/\s+/g, '-').toLowerCase() + '.yaml';
        },

        // Setup CodeMirror editor
        setupEditor() {
            const container = document.getElementById('score-editor');
            if (!container || !window.CodeMirror6) {
                console.error('CodeMirror container or CodeMirror6 not found');
                return;
            }

            try {
                const { EditorState, Compartment, EditorView } = window.CodeMirror6;

                // Create a compartment for decorations (allows reconfiguration)
                this._decorationCompartment = new Compartment();

                // Create initial state with validation decorations compartment
                const state = EditorState.create({
                    doc: this.content || this.getDefaultContent(),
                    extensions: [
                        window.CodeMirror6.basicSetup,
                        window.CodeMirror6.yaml(),
                        // Compartment starts with empty decorations
                        this._decorationCompartment.of([]),
                    ]
                });

                // Create editor view
                this.editor = new EditorView({
                    state: state,
                    parent: container,
                    dispatch: (transaction) => {
                        this.handleEditorChange(transaction);
                    }
                });

                this.updateStats();
                this.validateScore();

                console.log('Score editor initialized successfully');
            } catch (error) {
                console.error('Failed to initialize score editor:', error);
                this.showError('Editor initialization failed', error.message);
            }
        },

        // Handle editor content changes
        handleEditorChange(transaction) {
            // Apply the transaction to update the editor view
            this.editor.update([transaction]);

            // If there were document changes, trigger validation
            if (transaction.docChanged) {
                this.content = this.editor.state.doc.toString();
                this.updateStats();

                // Debounced validation (300ms for responsive feedback)
                clearTimeout(this._validationTimeout);
                this._validationTimeout = setTimeout(() => {
                    this.validateScore();
                }, 300);
            }
        },

        // Update editor statistics
        updateStats() {
            if (!this.editor) return;

            const doc = this.editor.state.doc;
            const selection = this.editor.state.selection.main;

            this.characterCount = doc.length;
            this.lineCount = doc.lines;

            // Calculate current line and column
            const pos = selection.head;
            const line = doc.lineAt(pos);
            this.currentLine = line.number;
            this.currentColumn = pos - line.from + 1;
        },

        // Validate score content with real-time API validation
        async validateScore() {
            if (this.isValidating) return;

            this.isValidating = true;
            this.errors = [];
            this.errorCount = 0;

            try {
                // Ensure content is a string (may be a Doc object from CodeMirror)
                const contentStr = (typeof this.content === 'string')
                    ? this.content
                    : (this.content?.toString ? this.content.toString() : '');
                const content = contentStr.trim();

                if (!content) {
                    this.validationStatus = 'warning';
                    this.validationMessage = 'Empty configuration';
                    this.clearErrorMarkers();
                    return;
                }

                // Call the validation API for comprehensive checking
                const response = await fetch('/api/scores/validate', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        content: content,
                        filename: 'editor-config.yaml'
                    })
                });

                if (!response.ok) {
                    throw new Error(`Validation API error: ${response.status}`);
                }

                const result = await response.json();

                // Process validation results
                this.processValidationResult(result);

            } catch (error) {
                console.error('Validation error:', error);
                this.validationStatus = 'invalid';
                this.validationMessage = 'Validation service unavailable';
                this.errors = [{
                    id: Date.now(),
                    line: 1,
                    message: 'Validation service error',
                    details: error.message,
                    severity: 'ERROR'
                }];
                this.errorCount = 1;
                this.showErrors = true;
                this.clearErrorMarkers();
            } finally {
                this.isValidating = false;
            }
        },

        // Process validation API response
        processValidationResult(result) {
            this.errors = result.issues || [];
            this.errorCount = result.counts?.ERROR || 0;
            const warningCount = result.counts?.WARNING || 0;
            const totalIssues = this.errorCount + warningCount;

            // Store config summary from validation response
            this.configSummary = result.config_summary || null;

            // Determine validation status
            if (this.errorCount > 0) {
                this.validationStatus = 'invalid';
                this.validationMessage = `${this.errorCount} error(s)`;
            } else if (warningCount > 0) {
                this.validationStatus = 'warning';
                this.validationMessage = `${warningCount} warning(s)`;
            } else {
                this.validationStatus = 'valid';
                this.validationMessage = 'Configuration valid';
            }

            // Show/hide error panel
            this.showErrors = totalIssues > 0;

            // Add error markers to editor
            this.updateErrorMarkers(this.errors);
        },

        // Update error markers in the editor using Compartment reconfiguration
        updateErrorMarkers(errors) {
            if (!this.editor || !this._decorationCompartment) return;

            const { Decoration, EditorView } = window.CodeMirror6;

            // Store current decorations for reference
            this._currentDecorations = errors;

            // If no errors, clear all decorations
            if (!errors.length) {
                this.clearErrorMarkers();
                return;
            }

            const decorations = [];

            errors.forEach(error => {
                const lineNum = (error.line || 1);  // 1-based line number

                try {
                    const doc = this.editor.state.doc;
                    if (lineNum < 1 || lineNum > doc.lines) return;

                    const lineInfo = doc.line(lineNum);
                    const from = lineInfo.from;

                    // Create line decoration based on severity
                    const severityClass = error.severity === 'ERROR' ? 'cm-validation-error-line' :
                                        error.severity === 'WARNING' ? 'cm-validation-warning-line' :
                                        'cm-validation-info-line';

                    decorations.push(
                        Decoration.line({
                            class: severityClass,
                            attributes: {
                                title: `${error.severity}: ${error.message || error.description}`
                            }
                        }).range(from)
                    );

                } catch (e) {
                    console.warn(`Could not mark line ${lineNum}:`, e);
                }
            });

            // Sort decorations by position (required by CodeMirror 6)
            decorations.sort((a, b) => a.from - b.from);

            // Create decoration set and apply via compartment reconfiguration
            const decorationSet = Decoration.set(decorations);

            // Create a view plugin that provides the decorations
            const decorationExtension = EditorView.decorations.of(decorationSet);

            // Reconfigure the compartment with new decorations
            this.editor.dispatch({
                effects: this._decorationCompartment.reconfigure(decorationExtension)
            });
        },

        // Clear all error markers
        clearErrorMarkers() {
            if (!this.editor || !this._decorationCompartment) return;

            this._currentDecorations = [];

            // Reconfigure compartment with empty extensions
            this.editor.dispatch({
                effects: this._decorationCompartment.reconfigure([])
            });
        },

        // Perform basic validation checks
        performBasicValidation(content) {
            const errors = [];
            const lines = content.split('\n');

            // Check for required fields
            const hasName = content.includes('name:');
            const hasSheets = content.includes('sheets:');
            const hasBackend = content.includes('backend:');

            if (!hasName) {
                errors.push({
                    id: Date.now() + 1,
                    line: 1,
                    message: 'Missing required field: name',
                    details: 'Job configuration must include a name field'
                });
            }

            if (!hasSheets) {
                errors.push({
                    id: Date.now() + 2,
                    line: 1,
                    message: 'Missing required field: sheets',
                    details: 'Job configuration must include at least one sheet'
                });
            }

            if (!hasBackend) {
                errors.push({
                    id: Date.now() + 3,
                    line: 1,
                    message: 'Missing required field: backend',
                    details: 'Job configuration must specify a backend type'
                });
            }

            // Basic YAML syntax check (simplified)
            lines.forEach((line, index) => {
                const trimmed = line.trim();
                if (trimmed && !trimmed.startsWith('#')) {
                    // Check for basic YAML syntax issues
                    if (trimmed.includes(':') && !trimmed.match(/^[\w\-_\s]+:/)) {
                        errors.push({
                            id: Date.now() + index + 100,
                            line: index + 1,
                            message: 'Potential YAML syntax error',
                            details: 'Check key format and indentation'
                        });
                    }
                }
            });

            return { errors };
        },

        // Template management
        loadTemplate() {
            this.showTemplateModal = true;
        },

        insertTemplate(template) {
            this.content = template.content;

            if (this.editor) {
                const newState = window.CodeMirror6.EditorState.create({
                    doc: template.content,
                    extensions: [
                        window.CodeMirror6.basicSetup,
                        window.CodeMirror6.yaml(),
                        this._decorationCompartment.of([]),
                    ]
                });

                this.editor.setState(newState);
                this.updateStats();
                this.validateScore();
            }

            this.showTemplateModal = false;

            // Notify user
            if (window.Alpine && window.Alpine.store('app')) {
                window.Alpine.store('app').addNotification(
                    'Template Loaded',
                    `${template.name} template inserted successfully`,
                    'success',
                    3000
                );
            }
        },

        // Create new empty score
        newScore() {
            const defaultContent = this.getDefaultContent();
            this.content = defaultContent;

            if (this.editor) {
                const newState = window.CodeMirror6.EditorState.create({
                    doc: defaultContent,
                    extensions: [
                        window.CodeMirror6.basicSetup,
                        window.CodeMirror6.yaml(),
                        this._decorationCompartment.of([]),
                    ]
                });

                this.editor.setState(newState);
                this.updateStats();
                this.validateScore();
            }
        },

        // Get default score content
        getDefaultContent() {
            return `# Mozart Job Configuration
name: "new-job"
description: "Description of the job"

backend:
  type: "claude_cli"

sheets:
  - name: "main"
    description: "Main task"
    command: "echo 'Hello from Mozart'"

timeout_seconds: 300
workspace: "./workspace"
`;
        },

        // Update editor mode (YAML/JSON)
        updateEditorMode() {
            // In a full implementation, this would switch language support
            console.log(`Editor mode changed to: ${this.editorMode}`);
        },

        // Toggle fullscreen mode
        toggleFullscreen() {
            this.isFullscreen = !this.isFullscreen;

            if (this.isFullscreen) {
                document.body.style.overflow = 'hidden';
            } else {
                document.body.style.overflow = '';
            }

            // Refresh editor after fullscreen toggle
            this.$nextTick(() => {
                if (this.editor && this.editor.dom) {
                    this.editor.requestMeasure();
                }
            });
        },

        // Setup keyboard and other event listeners
        setupEventListeners() {
            // Escape to exit fullscreen
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && this.isFullscreen) {
                    this.toggleFullscreen();
                }

                // Ctrl+S to validate (prevent browser save)
                if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                    e.preventDefault();
                    this.validateScore();
                }
            });
        },

        // Error handling
        showError(title, message) {
            if (window.Alpine && window.Alpine.store('app')) {
                window.Alpine.store('app').addNotification(title, message, 'error', 8000);
            } else {
                console.error(`${title}: ${message}`);
            }
        },

        // File import from local filesystem
        openFile(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (e) => {
                this.setContent(e.target.result);
                this.currentFileName = file.name;

                if (window.Alpine && window.Alpine.store('app')) {
                    window.Alpine.store('app').addNotification(
                        'File Loaded',
                        `Opened ${file.name}`,
                        'success',
                        3000
                    );
                }
            };
            reader.readAsText(file);

            // Reset input so the same file can be re-opened
            event.target.value = '';
        },

        // Set editor content programmatically
        setContent(text) {
            this.content = text;
            if (this.editor) {
                const newState = window.CodeMirror6.EditorState.create({
                    doc: text,
                    extensions: [
                        window.CodeMirror6.basicSetup,
                        window.CodeMirror6.yaml(),
                        this._decorationCompartment.of([]),
                    ]
                });
                this.editor.setState(newState);
                this.updateStats();
                this.validateScore();
            }
        },

        // Get current editor content as string
        getContent() {
            if (this.editor) {
                return this.editor.state.doc.toString();
            }
            return typeof this.content === 'string' ? this.content : '';
        },

        // Download current content as YAML file
        downloadScore() {
            const content = this.getContent();
            if (!content.trim()) return;

            const blob = new Blob([content], { type: 'application/x-yaml' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = this.currentFileName || 'mozart-score.yaml';
            a.click();
            URL.revokeObjectURL(url);

            if (window.Alpine && window.Alpine.store('app')) {
                window.Alpine.store('app').addNotification(
                    'Score Downloaded',
                    `Saved as ${a.download}`,
                    'success',
                    3000
                );
            }
        },

        // Submit score to conductor — opens confirmation modal
        submitScore() {
            if (this.validationStatus !== 'valid') {
                this.showError('Cannot Submit', 'Score must pass validation before submitting');
                return;
            }
            this.showSubmitModal = true;
        },

        // Actually submit after user confirms
        async confirmSubmit() {
            this.showSubmitModal = false;
            this.isSubmitting = true;

            try {
                const content = this.getContent();
                const response = await fetch('/api/scores/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: content })
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({ detail: response.statusText }));
                    throw new Error(error.detail || `HTTP ${response.status}`);
                }

                const result = await response.json();

                if (window.Alpine && window.Alpine.store('app')) {
                    window.Alpine.store('app').addNotification(
                        'Score Submitted',
                        `Job '${result.job_name}' started (${result.job_id})`,
                        'success',
                        5000
                    );
                }

                // Navigate to job details page
                window.location.href = `/jobs/${result.job_id}/details`;

            } catch (error) {
                this.showError('Submission Failed', error.message);
            } finally {
                this.isSubmitting = false;
            }
        },

        // Cleanup when component is destroyed
        destroy() {
            if (this.editor) {
                this.editor.destroy();
                this.editor = null;
            }

            document.body.style.overflow = '';

            if (this._validationTimeout) {
                clearTimeout(this._validationTimeout);
            }
        }
    };
};

// Console welcome message for developers
console.log(`
🎵 Mozart Dashboard v0.1.0
Built with HTMX + Alpine.js
Global utilities: window.MozartDashboard
Alpine store: Alpine.store('app')
Score Editor: window.scoreEditor()
`);