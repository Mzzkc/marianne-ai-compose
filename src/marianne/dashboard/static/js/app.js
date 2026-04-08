/* Marianne Dashboard — Alpine.js Application */

document.addEventListener('alpine:init', () => {
    Alpine.data('marianneApp', () => ({
        darkMode: localStorage.getItem('darkMode') === 'true' ||
                  (!localStorage.getItem('darkMode') && window.matchMedia('(prefers-color-scheme: dark)').matches),
        mobileMenu: false,
        notifications: [],
        _notifId: 0,

        init() {
            // Apply dark mode class on init
            if (this.darkMode) {
                document.documentElement.classList.add('dark');
            }

            // Watch dark mode changes
            this.$watch('darkMode', (val) => {
                document.documentElement.classList.toggle('dark', val);
                localStorage.setItem('darkMode', val);
            });
        },

        addNotification(message, type = 'info') {
            const id = ++this._notifId;
            this.notifications.push({ id, message, type });
            // Auto-dismiss after 5 seconds
            setTimeout(() => this.removeNotification(id), 5000);
        },

        removeNotification(id) {
            this.notifications = this.notifications.filter(n => n.id !== id);
        }
    }));
});

/* Helper to dispatch notifications via Alpine.js v3 API */
function notifyUser(message, type) {
    const el = document.querySelector('[x-data]');
    if (el && el._x_dataStack) {
        Alpine.$data(el).addNotification(message, type);
    }
}

/* htmx error handling */
document.addEventListener('htmx:responseError', (event) => {
    const status = event.detail.xhr?.status;
    const msg = status === 0
        ? 'Network error — is the server running?'
        : `Request failed (${status})`;
    notifyUser(msg, 'error');
});

/* htmx connection error */
document.addEventListener('htmx:sendError', () => {
    notifyUser('Connection lost — retrying...', 'warning');
});

/* Conductor status response handler — update dot color, prevent swap */
document.addEventListener('htmx:beforeSwap', (event) => {
    if (event.detail.pathInfo?.requestPath === '/api/jobs/daemon/status') {
        const dot = document.getElementById('conductor-dot');
        if (dot) {
            try {
                const data = JSON.parse(event.detail.xhr?.responseText || '{}');
                if (data.connected) {
                    dot.className = 'w-2 h-2 rounded-full bg-green-500 animate-pulse-dot';
                } else {
                    dot.className = 'w-2 h-2 rounded-full bg-red-500';
                }
            } catch (e) {
                if (event.detail.xhr?.status === 200) {
                    dot.className = 'w-2 h-2 rounded-full bg-green-500 animate-pulse-dot';
                } else {
                    dot.className = 'w-2 h-2 rounded-full bg-red-500';
                }
            }
        }
        event.detail.shouldSwap = false;
    }
});
