document.addEventListener('DOMContentLoaded', () => {
    bindAjaxForms();
    bindModals();
    bindOverlayBuilder();
});

function bindAjaxForms() {
    document.querySelectorAll('.ajax-form').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const targetId = form.dataset.target;
            const target = targetId ? document.getElementById(targetId) : null;
            if (target) {
                target.classList.add('loading');
                target.textContent = 'Working...';
            }

            if (form.classList.contains('overlay-builder-form')) {
                syncOverlayPlacements();
            }

            try {
                const response = await fetch(form.action, {
                    method: form.method || 'POST',
                    body: new FormData(form),
                });
                const payload = await response.json();
                renderPayload(target, payload);
            } catch (error) {
                renderPayload(target, { ok: false, message: error.message || 'Request failed.' });
            }
        });
    });
}

function bindModals() {
    document.querySelectorAll('[data-open-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const modal = document.getElementById(button.dataset.openModal);
            if (modal) {
                modal.hidden = false;
            }
        });
    });

    document.querySelectorAll('[data-close-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const modal = document.getElementById(button.dataset.closeModal);
            if (modal) {
                modal.hidden = true;
            }
        });
    });

    document.querySelectorAll('.modal-shell').forEach((modal) => {
        modal.addEventListener('click', (event) => {
            if (event.target === modal) {
                modal.hidden = true;
            }
        });
    });
}

const overlayState = {
    baseType: null,
    baseImage: null,
    videoPreview: null,
    items: [],
    activeIndex: -1,
    dragOffsetX: 0,
    dragOffsetY: 0,
};

function bindOverlayBuilder() {
    const canvas = document.getElementById('overlay-canvas');
    if (!canvas) {
        return;
    }

    const ctx = canvas.getContext('2d');
    const baseInput = document.getElementById('base-media-input');
    const overlayInput = document.getElementById('overlay-media-input');

    baseInput?.addEventListener('change', async (event) => {
        const file = event.target.files?.[0];
        if (!file) {
            return;
        }

        if (file.type.startsWith('video/')) {
            overlayState.baseType = 'video';
            overlayState.videoPreview = await makeVideoPreview(file);
            overlayState.baseImage = overlayState.videoPreview;
        } else {
            overlayState.baseType = 'image';
            overlayState.baseImage = await fileToImage(file);
        }

        fitCanvasToImage(canvas, overlayState.baseImage);
        drawOverlayCanvas(ctx, canvas);
    });

    overlayInput?.addEventListener('change', async (event) => {
        const files = Array.from(event.target.files || []);
        for (const file of files) {
            const image = await fileToImage(file);
            overlayState.items.push({
                fileName: file.name,
                x: 40 + overlayState.items.length * 20,
                y: 40 + overlayState.items.length * 20,
                width: Math.max(120, Math.round(image.width * 0.35)),
                height: Math.max(120, Math.round(image.height * 0.35)),
                opacity: 1,
                image,
            });
        }
        drawOverlayCanvas(ctx, canvas);
    });

    canvas.addEventListener('pointerdown', (event) => {
        const point = getCanvasPoint(canvas, event);
        overlayState.activeIndex = findOverlayAtPoint(point.x, point.y);
        if (overlayState.activeIndex >= 0) {
            const item = overlayState.items[overlayState.activeIndex];
            overlayState.dragOffsetX = point.x - item.x;
            overlayState.dragOffsetY = point.y - item.y;
            canvas.setPointerCapture(event.pointerId);
        }
    });

    canvas.addEventListener('pointermove', (event) => {
        if (overlayState.activeIndex < 0) {
            return;
        }
        const point = getCanvasPoint(canvas, event);
        const item = overlayState.items[overlayState.activeIndex];
        item.x = Math.round(point.x - overlayState.dragOffsetX);
        item.y = Math.round(point.y - overlayState.dragOffsetY);
        drawOverlayCanvas(ctx, canvas);
    });

    canvas.addEventListener('pointerup', () => {
        overlayState.activeIndex = -1;
    });
}

function syncOverlayPlacements() {
    const hiddenInput = document.getElementById('placements-json');
    if (!hiddenInput) {
        return;
    }
    hiddenInput.value = JSON.stringify(
        overlayState.items.map((item) => ({
            x: item.x,
            y: item.y,
            width: item.width,
            height: item.height,
            opacity: item.opacity,
        }))
    );
}

function drawOverlayCanvas(ctx, canvas) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (overlayState.baseImage) {
        ctx.drawImage(overlayState.baseImage, 0, 0, canvas.width, canvas.height);
    } else {
        ctx.fillStyle = '#f4efe8';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    overlayState.items.forEach((item, index) => {
        ctx.save();
        ctx.globalAlpha = item.opacity;
        ctx.drawImage(item.image, item.x, item.y, item.width, item.height);
        ctx.restore();

        ctx.strokeStyle = index === overlayState.activeIndex ? '#ff8b6a' : 'rgba(22,32,50,0.28)';
        ctx.lineWidth = 2;
        ctx.strokeRect(item.x, item.y, item.width, item.height);
    });
}

function findOverlayAtPoint(x, y) {
    for (let index = overlayState.items.length - 1; index >= 0; index -= 1) {
        const item = overlayState.items[index];
        if (x >= item.x && x <= item.x + item.width && y >= item.y && y <= item.y + item.height) {
            return index;
        }
    }
    return -1;
}

function fitCanvasToImage(canvas, image) {
    if (!image) {
        return;
    }
    canvas.width = image.width;
    canvas.height = image.height;
}

function getCanvasPoint(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
        x: (event.clientX - rect.left) * scaleX,
        y: (event.clientY - rect.top) * scaleY,
    };
}

function fileToImage(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = reject;
            image.src = reader.result;
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

function makeVideoPreview(file) {
    return new Promise((resolve, reject) => {
        const video = document.createElement('video');
        video.preload = 'metadata';
        video.muted = true;
        video.playsInline = true;
        video.src = URL.createObjectURL(file);
        video.onloadeddata = () => {
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth || 720;
            canvas.height = video.videoHeight || 1280;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const image = new Image();
            image.onload = () => {
                URL.revokeObjectURL(video.src);
                resolve(image);
            };
            image.onerror = reject;
            image.src = canvas.toDataURL('image/png');
        };
        video.onerror = reject;
    });
}

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
        payload.outputs.forEach((item) => lines.push(`Download: ${item.download_url}`));
    }
    if (payload.saved && payload.saved.length) {
        lines.push('');
        lines.push(`Saved: ${payload.saved.join(', ')}`);
    }
    target.textContent = lines.join('\n');
}
