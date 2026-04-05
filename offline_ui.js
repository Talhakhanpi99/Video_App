document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".navbar-toggler").forEach((button) => {
        button.addEventListener("click", () => {
            const targetSelector = button.getAttribute("data-bs-target");
            if (!targetSelector) {
                return;
            }

            const target = document.querySelector(targetSelector);
            if (target) {
                target.classList.toggle("show");
            }
        });
    });

    document.querySelectorAll('[data-bs-toggle="collapse"]').forEach((button) => {
        button.addEventListener("click", () => {
            const targetSelector = button.getAttribute("data-bs-target");
            if (!targetSelector) {
                return;
            }

            const target = document.querySelector(targetSelector);
            if (!target) {
                return;
            }

            const parentSelector = target.getAttribute("data-bs-parent");
            if (parentSelector) {
                document.querySelectorAll(`${parentSelector} .accordion-collapse.show`).forEach((panel) => {
                    if (panel !== target) {
                        panel.classList.remove("show");
                        const trigger = document.querySelector(`[data-bs-target="#${panel.id}"]`);
                        if (trigger) {
                            trigger.classList.add("collapsed");
                        }
                    }
                });
            }

            const isVisible = target.classList.contains("show");
            target.classList.toggle("show", !isVisible);
            button.classList.toggle("collapsed", isVisible);
        });
    });

    document.querySelectorAll(".alert-dismissible .btn-close").forEach((button) => {
        button.addEventListener("click", () => {
            const alert = button.closest(".alert");
            if (alert) {
                alert.remove();
            }
        });
    });
});
