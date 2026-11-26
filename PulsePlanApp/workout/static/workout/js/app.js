const dashboardId = 'dashboard-content';
const calendarStatus = document.getElementById('calendar-status');
let calendarRequest;
const lockedForms = new Map();

function announceCalendarError() {
    let notice = document.getElementById('calendar-navigation-error');
    if (!notice) {
        notice = document.createElement('p');
        notice.id = 'calendar-navigation-error';
        notice.className = 'alert alert-danger';
        notice.setAttribute('role', 'alert');
        document.getElementById(dashboardId)?.before(notice);
    }
    notice.textContent = 'Не удалось обновить календарь. Проверьте соединение и попробуйте ещё раз.';
}

function keepSelectedDayInView(dashboard, scrollLeft = 0) {
    const calendar = dashboard?.querySelector('.calendar-scroll');
    if (!calendar || calendar.scrollWidth <= calendar.clientWidth) return;

    calendar.scrollLeft = scrollLeft;
    const selected = calendar.querySelector('.calendar-cell.is-selected');
    if (!selected) return;

    const cellBounds = selected.getBoundingClientRect();
    const calendarBounds = calendar.getBoundingClientRect();
    if (cellBounds.left < calendarBounds.left || cellBounds.right > calendarBounds.right) {
        calendar.scrollLeft += cellBounds.left - calendarBounds.left;
    }
}

async function loadCalendar(url, pushHistory, source) {
    calendarRequest?.abort();
    const request = new AbortController();
    calendarRequest = request;
    const navigationUrl = new URL(url, location.href);
    const targetId = navigationUrl.hash.slice(1);
    document.getElementById('calendar-navigation-error')?.remove();
    try {
        const response = await fetch(navigationUrl, {signal: request.signal});
        if (response.redirected) {
            location.assign(response.url);
            return;
        }
        if (!response.ok) throw new Error('Calendar request failed');

        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (request.signal.aborted) return;
        const dashboard = page.getElementById(dashboardId);
        if (!dashboard) throw new Error('Calendar content is missing');

        const current = document.getElementById(dashboardId);
        const scrollLeft = current.querySelector('.calendar-scroll').scrollLeft;
        current.replaceWith(dashboard);
        keepSelectedDayInView(dashboard, scrollLeft);
        document.title = page.title;
        if (pushHistory && navigationUrl.href !== location.href) history.pushState({}, '', navigationUrl);
        const heading = document.getElementById('selected-day');
        calendarStatus.textContent = heading.textContent.trim();

        if (targetId) {
            const target = document.getElementById(targetId) || heading;
            target.focus({preventScroll: true});
            target.scrollIntoView({block: 'start'});
        } else if (source) {
            const controls = [...dashboard.querySelectorAll('.calendar-controls a')];
            const equivalent = controls.find(control => (
                control.getAttribute('aria-label') === source.getAttribute('aria-label')
                && control.textContent.trim() === source.textContent.trim()
            ));
            (equivalent || dashboard.querySelector('[aria-current="date"]'))?.focus({preventScroll: true});
        }
    } catch (error) {
        if (!request.signal.aborted) announceCalendarError();
    }
}

document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a');
    if (link?.matches('[data-calendar-navigation]')) {
        event.preventDefault();
        loadCalendar(link.href, true, link);
    } else if (!link) {
        const dateLink = event.target.closest('[data-calendar-cell]')?.querySelector('.calendar-day-number');
        if (dateLink) loadCalendar(dateLink.href, true, dateLink);
    }
});

window.addEventListener('popstate', () => {
    if (document.getElementById(dashboardId)) loadCalendar(location.href, false);
});

const modalElement = document.getElementById('deleteConfirmModal');
const deleteModal = new bootstrap.Modal(modalElement);
const confirmButton = document.getElementById('deleteConfirmButton');
let pendingDeletion;
let confirmedForm;

confirmButton.addEventListener('click', () => {
    if (!pendingDeletion) return;
    const {form, submitter} = pendingDeletion;
    pendingDeletion = null;
    deleteModal.hide();
    if (!form.isConnected) return;

    confirmedForm = form;
    form.requestSubmit(submitter || undefined);
    confirmedForm = null;
});

modalElement.addEventListener('hidden.bs.modal', () => {
    pendingDeletion?.submitter?.focus();
    pendingDeletion = null;
});

function lockForm(form, submitter, preserveSubmitter) {
    const buttons = Array.from(form.elements).filter(control =>
        !control.disabled && (control.type === 'submit' || control.type === 'image'));
    let submittedButton;
    if (preserveSubmitter && submitter?.name) {
        submittedButton = document.createElement('input');
        submittedButton.type = 'hidden';
        submittedButton.name = submitter.name;
        submittedButton.value = submitter.value;
        form.append(submittedButton);
    }
    buttons.forEach(button => { button.disabled = true; });
    form.setAttribute('aria-busy', 'true');
    lockedForms.set(form, {buttons, submittedButton});
}

function unlockForm(form) {
    const lock = lockedForms.get(form);
    if (!lock) return;
    lock.buttons.forEach(button => { button.disabled = false; });
    lock.submittedButton?.remove();
    form.removeAttribute('aria-busy');
    lockedForms.delete(form);
}

function showDownloadError(form, message) {
    let notice = form.querySelector('[data-download-error]');
    if (!notice) {
        notice = document.createElement('p');
        notice.dataset.downloadError = '';
        notice.className = 'alert alert-danger';
        notice.setAttribute('role', 'alert');
        form.prepend(notice);
    }
    notice.textContent = message;
}

async function downloadExport(form, data) {
    try {
        const response = await fetch(form.action, {method: 'POST', body: data});
        if (response.redirected && new URL(response.url).pathname !== location.pathname) {
            location.assign(response.url);
            return;
        }
        const disposition = response.headers.get('Content-Disposition');
        if (!response.ok || !disposition?.startsWith('attachment')) {
            const page = new DOMParser().parseFromString(await response.text(), 'text/html');
            const notice = page.querySelector('.app-main .alert-danger');
            showDownloadError(form, notice?.textContent.trim() || 'Не удалось сформировать файл.');
            return;
        }
        const downloadUrl = URL.createObjectURL(await response.blob());
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = disposition.match(/filename="([^"]+)"/)?.[1] || 'pulseplan_export';
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
    } catch {
        showDownloadError(form, 'Не удалось скачать файл. Проверьте соединение и попробуйте ещё раз.');
    } finally {
        unlockForm(form);
    }
}

document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const form = event.target;
    const submitter = event.submitter;
    if (lockedForms.has(form)) {
        event.preventDefault();
        return;
    }
    if (form.hasAttribute('data-delete-title') && confirmedForm !== form) {
        event.preventDefault();
        pendingDeletion = {form, submitter};
        document.getElementById('deleteConfirmText').textContent =
            'Подтвердите удаление: ' + form.dataset.deleteTitle + '.';
        document.getElementById('deleteConfirmDetails').textContent =
            form.dataset.deleteWarning ? ' ' + form.dataset.deleteWarning : '';
        deleteModal.show();
        return;
    }
    if (form.hasAttribute('data-download-form')) {
        event.preventDefault();
        form.querySelector('[data-download-error]')?.remove();
        const data = submitter ? new FormData(form, submitter) : new FormData(form);
        lockForm(form, submitter, false);
        downloadExport(form, data);
        return;
    }
    lockForm(form, submitter, true);
});

window.addEventListener('pageshow', () => {
    lockedForms.forEach((_, form) => unlockForm(form));
});

const formErrors = document.getElementById('form-errors');
if (formErrors && !document.querySelector('[autofocus]')) formErrors.focus();
document.querySelector('.entry-form-panel [aria-invalid="true"]')?.focus();
keepSelectedDayInView(document.getElementById(dashboardId));
