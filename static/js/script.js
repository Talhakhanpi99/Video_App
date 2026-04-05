document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.ajax-form').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const targetId = form.dataset.target;
            const target = targetId ? document.getElementById(targetId) : null;
            if (target) {
                target.classList.add('loading');
                target.textContent = 'Working...';
            }

            try {
                const response = await fetch(form.action, {
                    method: form.method || 'POST',
                    body: new FormData(form),
                });
                const payload = await response.json();
                renderPayload(target, payload);
                if (payload.ok && form.getAttribute('enctype') !== 'multipart/form-data') {
                    const keepKeys = new Set(['email', 'activation_key']);
                    form.querySelectorAll('input, textarea').forEach((input) => {
                        if (!keepKeys.has(input.name) && input.type !== 'hidden') {
                            input.value = '';
                        }
                    });
                }
            } catch (error) {
                renderPayload(target, { ok: false, message: error.message || 'Request failed.' });
            }
        });
    });

    const refreshButton = document.querySelector('[data-action="refresh-status"]');
    if (refreshButton) {
        refreshButton.addEventListener('click', async () => {
            refreshButton.disabled = true;
            try {
                await fetch('/api/status');
                window.location.reload();
            } finally {
                refreshButton.disabled = false;
            }
        });
    }
});

function renderPayload(target, payload) {
    if (!target) {
        return;
    }

    target.classList.remove('loading');
    const lines = [];
    lines.push(payload.ok ? 'Success' : 'Error');
    if (payload.message) {
        lines.push(payload.message);
    }
    if (payload.script_text) {
        lines.push('');
        lines.push(payload.script_text);
    }
    if (payload.output) {
        lines.push('');
        lines.push(`Download: ${payload.output.download_url}`);
    }
    if (Array.isArray(payload.outputs) && payload.outputs.length) {
        lines.push('');
        payload.outputs.forEach((item) => {
            lines.push(`Download: ${item.download_url}`);
        });
    }
    if (payload.saved && payload.saved.length) {
        lines.push('');
        lines.push(`Saved: ${payload.saved.join(', ')}`);
    }
    if (payload.preset) {
        lines.push('');
        lines.push(`Preset: x=${payload.preset.x}, y=${payload.preset.y}, width=${payload.preset.width}, height=${payload.preset.height}`);
    }
    target.textContent = lines.join('\n');
}
