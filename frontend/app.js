const workerId = new URLSearchParams(location.search).get('worker');
const isWorker = workerId === 'A' || workerId === 'B';
const statusLabels = {
  eating: 'Guests are dining',
  needs_check: 'Ready for a check-in',
  being_served: 'Being served',
  checked: 'Checked in',
};
let firstLoad = true;
let lastEventId = 0;
let toastTimer;

const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

function announce(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
}

async function post(url, body = {}) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'That action could not be completed.');
    await refresh();
    return true;
  } catch (error) {
    announce(error.message || 'ServiceMind is not connected.');
    return false;
  }
}

function statusClass(value) {
  return String(value).replace(/[^a-z_]/g, '');
}

function render(state, events) {
  const table = state.table;
  const currentWorker = state.workers.find((worker) => worker.id === workerId);
  document.title = isWorker ? `Worker ${workerId} · ServiceMind` : 'ServiceMind · Restaurant service, in sync';
  $('#view-eyebrow').textContent = isWorker ? `Restaurant · Worker ${workerId}` : 'Restaurant · Service overview';
  $('#page-title').textContent = isWorker ? `Hi ${currentWorker?.name || `Worker ${workerId}`}. Your team is in sync.` : 'Good service starts with a good moment.';
  $('#page-copy').textContent = isWorker ? 'See the shared floor, handle your next step, and keep the whole team up to date.' : 'A shared view of the floor, so the right person can check in at the right time.';
  $('.connection-pill').setAttribute('aria-label', 'Connected to the local ServiceMind server');
  $('#connection-label').textContent = 'Live · updates every second';
  document.querySelectorAll('.view-switch a').forEach((link) => {
    const view = link.dataset.view;
    if ((!isWorker && view === 'admin') || (isWorker && view === workerId)) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  $('#table-status').textContent = statusLabels[table.status] || table.status;
  $('#table-chip').textContent = statusLabels[table.status] || table.status;
  $('#table-chip').className = `status-chip ${statusClass(table.status)}`;
  $('#plate-summary').textContent = `${table.customers.length} guests · Plates A and B`;
  $('#available-count').textContent = state.workers.filter((worker) => worker.availability === 'available').length;
  $('#request-count').textContent = state.requests.filter((request) => request.status === 'open').length;

  $('#guest-list').innerHTML = table.customers.map((customer) => `
    <div class="guest-row">
      <div class="guest-avatar" aria-hidden="true">${escapeHTML(customer.plate_id)}</div>
      <div class="guest-copy"><strong>${escapeHTML(customer.name)}</strong><span>Plate ${escapeHTML(customer.plate_id)}</span></div>
      <span class="food-chip ${statusClass(customer.food_state)}">${escapeHTML(customer.food_state)}</span>
    </div>`).join('');

  $('#mock-controls').hidden = isWorker;
  if (!isWorker) {
    $('#plate-controls').innerHTML = table.customers.map((customer) => `
      <div class="plate-control">
        <div class="plate-control-top"><span>Plate ${escapeHTML(customer.plate_id)}</span><span class="food-chip ${statusClass(customer.food_state)}">${escapeHTML(customer.food_state)}</span></div>
        <div class="plate-control-buttons" aria-label="Mock observation for plate ${escapeHTML(customer.plate_id)}">
        ${['food', 'empty', 'unknown'].map((value) => `<button type="button" data-plate="${escapeHTML(customer.plate_id)}" data-food-state="${value}">${value[0].toUpperCase()}${value.slice(1)}</button>`).join('')}
        </div>
      </div>`).join('');
  }

  const visibleTasks = state.tasks.filter((task) => !['cancelled'].includes(task.status));
  $('#task-count').textContent = visibleTasks.filter((task) => ['pending', 'offered', 'assigned'].includes(task.status)).length;
  $('#task-list').innerHTML = visibleTasks.length ? visibleTasks.map((task) => {
    const assignment = task.status === 'offered' ? `Offered to Worker ${task.offered_to}` : task.worker_id ? `Worker ${task.worker_id}` : 'Waiting for an available teammate';
    return `<article class="task-card"><div class="task-main"><span class="task-icon" aria-hidden="true">✓</span><div class="task-copy"><strong>Check in at Table 01</strong><p>${escapeHTML(task.reason)}<br>${escapeHTML(assignment)}</p></div></div><div class="task-meta"><span class="status-chip ${statusClass(task.status)}">${escapeHTML(task.status)}</span></div></article>`;
  }).join('') : '<div class="empty-state">No tasks yet. The team will see a check-in here when the demo observations call for one.</div>';

  $('#worker-list').innerHTML = state.workers.map((worker) => `
    <div class="worker-row"><div class="worker-identity"><span class="worker-avatar">${escapeHTML(worker.id)}</span><div class="worker-copy"><strong>${escapeHTML(worker.name)}</strong><span>${worker.id === 'A' ? 'Dining room' : 'Floor team'}</span></div></div><span class="worker-state ${statusClass(worker.availability)}">${escapeHTML(worker.availability)}</span></div>`).join('');

  const openRequests = state.requests.filter((request) => request.status === 'open');
  $('#request-list').innerHTML = openRequests.length ? openRequests.map((request) => `
    <div class="request-item"><div class="request-title"><span class="request-symbol" aria-hidden="true">${request.type === 'dessert' ? '🍰' : '💧'}</span><span>${escapeHTML(request.type[0].toUpperCase() + request.type.slice(1))}</span></div><small>Table 01 · Worker ${escapeHTML(request.worker_id)}</small></div>`).join('') : '<div class="empty-state">No guest requests to follow up on.</div>';

  $('#event-list').innerHTML = events.slice(-8).reverse().map((item) => `
    <li><p>${escapeHTML(item.message)}</p><time datetime="${escapeHTML(item.at)}">${new Date(item.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></li>`).join('') || '<li><p>Waiting for the first floor update.</p></li>';

  $('#worker-actions').hidden = !isWorker;
  if (isWorker && currentWorker) {
    $('#worker-chip').textContent = currentWorker.availability;
    $('#worker-chip').className = `status-chip ${statusClass(currentWorker.availability)}`;
    const offered = state.tasks.find((task) => task.status === 'offered' && task.offered_to === workerId);
    const assigned = state.tasks.find((task) => task.status === 'assigned' && task.worker_id === workerId);
    if (offered) {
      $('#my-task').innerHTML = `<article class="task-card"><div class="task-main"><span class="task-icon" aria-hidden="true">✓</span><div class="task-copy"><strong>Check in at Table 01</strong><p>${escapeHTML(offered.reason)}</p></div></div></article><div class="worker-action-row"><button class="button button-outline" data-task-action="accept" data-task="${escapeHTML(offered.id)}" type="button">Accept check-in</button><button class="button button-quiet" data-task-action="decline" data-task="${escapeHTML(offered.id)}" type="button">Decline</button></div>`;
    } else if (assigned) {
      $('#my-task').innerHTML = `<article class="task-card"><div class="task-main"><span class="task-icon" aria-hidden="true">✓</span><div class="task-copy"><strong>Check in at Table 01</strong><p>${escapeHTML(assigned.reason)}<br>You are looking after this table.</p></div></div><span class="status-chip assigned">Assigned</span></article><div class="worker-action-row"><button class="button button-outline" data-task-action="complete" data-task="${escapeHTML(assigned.id)}" type="button">Complete check-in</button></div>`;
    } else {
      $('#my-task').innerHTML = '<div class="empty-state">No task for you right now. New check-ins will appear here automatically.</div>';
    }
    $('#availability-caption').textContent = currentWorker.availability === 'available' ? 'Ready for a teammate to offer a task' : 'You are marked busy';
    $('#availability-button').textContent = currentWorker.availability === 'available' ? 'Mark busy' : 'Mark available';
    $('#availability-button').disabled = Boolean(assigned && currentWorker.availability === 'busy');
  }

  if (!firstLoad && events.length) {
    const fresh = events.filter((item) => item.id > lastEventId);
    if (fresh.length) announce(fresh[fresh.length - 1].message);
  }
  if (events.length) lastEventId = Math.max(lastEventId, ...events.map((item) => item.id));
  firstLoad = false;
}

async function refresh() {
  try {
    const [stateResponse, eventResponse] = await Promise.all([fetch('/api/state'), fetch('/api/events')]);
    if (!stateResponse.ok || !eventResponse.ok) throw new Error('Local server response was not available.');
    const [state, events] = await Promise.all([stateResponse.json(), eventResponse.json()]);
    render(state, events);
  } catch {
    $('#connection-label').textContent = 'Reconnecting…';
  }
}

document.addEventListener('click', async (event) => {
  const observation = event.target.closest('[data-plate][data-food-state]');
  if (observation) {
    await post('/api/observations', { table_id: '1', plate_id: observation.dataset.plate, food_state: observation.dataset.foodState });
    return;
  }
  const taskAction = event.target.closest('[data-task-action]');
  if (taskAction) {
    const action = taskAction.dataset.taskAction;
    await post(`/api/tasks/${encodeURIComponent(taskAction.dataset.task)}/${action}`, { worker_id: workerId });
    return;
  }
  const requestButton = event.target.closest('[data-request]');
  if (requestButton && isWorker) {
    await post('/api/tables/1/requests', { worker_id: workerId, request_type: requestButton.dataset.request });
    return;
  }
  if (event.target.closest('#availability-button') && isWorker) {
    const state = await fetch('/api/state').then((response) => response.json());
    const currentWorker = state.workers.find((worker) => worker.id === workerId);
    await post(`/api/workers/${workerId}/availability`, { availability: currentWorker.availability === 'available' ? 'busy' : 'available' });
    return;
  }
  if (event.target.closest('#not-ready-button') && isWorker) {
    await post('/api/tables/1/correction', { correction: 'not_ready' });
    return;
  }
  if (event.target.closest('#reset-button') && !isWorker && confirm('Reset the demo state? The activity history will remain.')) {
    await post('/api/demo/reset');
  }
});

refresh();
setInterval(refresh, 1000);
