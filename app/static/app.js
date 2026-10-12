// Admin WebUI Interactive Controller

// If the admin session expires, send the browser to the WebUI login page
// (admin endpoints never send a Basic-auth challenge).
const _nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _nativeFetch(...args);
  if (res.status === 401 && !window.location.pathname.startsWith("/admin/login")) {
    window.location.href = "/admin/login";
  }
  return res;
};

function showCalcBanner(message, type = "info") {
  const banner = document.getElementById("calc-feedback-banner");
  if (!banner) return;
  banner.style.display = "block";
  if (type === "success") {
    banner.style.background = "var(--color-success-bg)";
    banner.style.border = "1px solid var(--color-success-border)";
    banner.style.color = "#4ade80";
  } else if (type === "danger" || type === "error") {
    banner.style.background = "var(--color-danger-bg)";
    banner.style.border = "1px solid var(--color-danger-border)";
    banner.style.color = "#f87171";
  } else {
    banner.style.background = "rgba(59, 130, 246, 0.12)";
    banner.style.border = "1px solid rgba(59, 130, 246, 0.3)";
    banner.style.color = "#60a5fa";
  }
  banner.innerText = message;
}

async function triggerPageCalc(missingOnly, clickedBtn = null) {
  const btn = clickedBtn || document.getElementById(missingOnly ? "btn-calc-missing" : "btn-calc-all") || (window.event && window.event.target ? window.event.target.closest("button") : null);
  const originalHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-block;width:12px;height:12px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;margin-right:6px;"></span> Starting...`;
  }

  // Clear previous banner
  const banner = document.getElementById("calc-feedback-banner");
  if (banner) banner.style.display = "none";

  // Immediately make the job status card visible with an active starting state
  const jobBox = document.getElementById("active-job-box");
  if (jobBox) {
    jobBox.style.display = "block";
    const badge = document.getElementById("job-status-badge");
    if (badge) {
      badge.innerText = "STARTING";
      badge.className = "status-badge status-running";
    }
    const currentBook = document.getElementById("job-current-book");
    if (currentBook) {
      currentBook.innerText = missingOnly ? "Checking catalog for books missing page counts..." : "Initializing full catalog recalculation...";
    }
  }

  try {
    const res = await fetch("/admin/api/calculate-pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ missing_only: missingOnly })
    });
    const data = await res.json();
    if (!res.ok) {
      const errMsg = "Error: " + (data.detail || "Failed to trigger page calculation");
      showCalcBanner(errMsg, "danger");
      if (jobBox) {
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          badge.innerText = "FAILED";
          badge.className = "status-badge status-failed";
        }
        const currentBook = document.getElementById("job-current-book");
        if (currentBook) currentBook.innerText = data.detail || "Error starting calculation";
      }
    } else if (data.status === "no_work") {
      const msg = data.message || "All books already have page counts calculated! If you wish to recalculate existing pages, use 'Recalculate All Pages'.";
      showCalcBanner(msg, "success");
      if (jobBox) {
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          badge.innerText = "UP TO DATE";
          badge.className = "status-badge status-completed";
        }
        const currentBook = document.getElementById("job-current-book");
        if (currentBook) {
          currentBook.innerText = "All catalog books already enriched with page counts.";
        }
      }
    } else if (data.status === "already_running") {
      showCalcBanner("A calculation job is already running.", "info");
    } else {
      showCalcBanner("Page calculation job started successfully.", "success");
      if (jobBox) {
        jobBox.style.display = "block";
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          badge.innerText = "RUNNING";
          badge.className = "status-badge status-running";
        }
        const currentBook = document.getElementById("job-current-book");
        if (currentBook) {
          currentBook.innerText = "Starting calculation...";
        }
      }
    }
  } catch (err) {
    const errText = "Request failed: " + err;
    showCalcBanner(errText, "danger");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
    pollStatus();
  }
}

async function stopPageCalc(clickedBtn = null) {
  const btn = clickedBtn || document.getElementById("btn-calc-stop") || (window.event && window.event.target ? window.event.target.closest("button") : null);
  const jobBox = document.getElementById("active-job-box");
  const originalHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerText = "Stopping...";
  }

  try {
    const res = await fetch("/admin/api/stop-calculation", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showCalcBanner(data.detail || "Failed to stop job", "danger");
    } else {
      showCalcBanner(data.message || "Page calculation job stopped.", "info");
      if (jobBox) {
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          badge.innerText = "STOPPED";
          badge.className = "status-badge status-idle";
        }
        const currentBook = document.getElementById("job-current-book");
        if (currentBook) {
          currentBook.innerText = "Calculation stopped by user";
        }
      }
    }
  } catch (err) {
    showCalcBanner("Request failed: " + err, "danger");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
    pollStatus();
  }
}

async function triggerSync() {
  const btn = document.getElementById("btn-sync") || (window.event && window.event.target);
  if (btn) {
    btn.disabled = true;
    btn.innerText = "Syncing...";
  }

  const logCard = document.getElementById("sync-log-card");
  if (logCard) logCard.style.display = "block";
  const logCont = document.getElementById("sync-log-container");
  if (logCont) {
    const timeStr = new Date().toTimeString().split(' ')[0];
    logCont.innerText = `[${timeStr}] Triggering Grimmory metadata synchronization...\n`;
  }
  const badge = document.getElementById("sync-status-badge");
  if (badge) {
    badge.innerText = "SYNCING";
    badge.className = "status-badge status-running";
  }

  try {
    const res = await fetch("/admin/api/sync", { method: "POST" });
    const data = await res.json();
    if (!res.ok) alert("Error: " + (data.detail || "Failed to trigger sync"));
  } catch (err) {
    alert("Request failed: " + err);
  } finally {
    pollStatus();
  }
}

async function clearSyncLogs() {
  try {
    await fetch("/admin/api/sync/clear-logs", { method: "POST" });
    const logCont = document.getElementById("sync-log-container");
    if (logCont) logCont.innerHTML = '<span style="color: var(--color-text-muted);">Log cleared.</span>';
    const logCard = document.getElementById("sync-log-card");
    if (logCard) logCard.style.display = "none";
  } catch (err) {
    console.debug("Failed to clear sync logs:", err);
  }
}

async function triggerReadSync() {
  const btn = document.getElementById("btn-read-sync") || (window.event && window.event.target);
  if (btn) {
    btn.disabled = true;
    btn.innerText = "Syncing...";
  }

  const logCard = document.getElementById("read-sync-log-card");
  if (logCard) logCard.style.display = "block";
  const logCont = document.getElementById("read-sync-log-container");
  if (logCont) {
    const timeStr = new Date().toTimeString().split(' ')[0];
    logCont.innerText = `[${timeStr}] Triggering user read status synchronization...\n`;
  }
  const badge = document.getElementById("read-sync-status-badge");
  if (badge) {
    badge.innerText = "SYNCING";
    badge.className = "status-badge status-running";
  }

  try {
    const res = await fetch("/admin/api/read-sync", { method: "POST" });
    const data = await res.json();
    if (!res.ok) alert("Error: " + (data.detail || "Failed to trigger read sync"));
  } catch (err) {
    alert("Request failed: " + err);
  } finally {
    pollStatus();
  }
}

async function clearReadSyncLogs() {
  try {
    await fetch("/admin/api/read-sync/clear-logs", { method: "POST" });
    const logCont = document.getElementById("read-sync-log-container");
    if (logCont) logCont.innerHTML = '<span style="color: var(--color-text-muted);">Log cleared.</span>';
    const logCard = document.getElementById("read-sync-log-card");
    if (logCard) logCard.style.display = "none";
  } catch (err) {
    console.debug("Failed to clear read sync logs:", err);
  }
}

let _isPolling = false;
async function pollStatus() {
  if (_isPolling) return;
  _isPolling = true;
  try {
    const res = await fetch("/admin/api/status");
    if (!res.ok) return;
    const data = await res.json();

    // Update Stats KPIs
    if (document.getElementById("stat-books-count")) {
      document.getElementById("stat-books-count").innerText = Number(data.stats.books_count).toLocaleString();
    }
    if (document.getElementById("stat-series-count")) {
      document.getElementById("stat-series-count").innerText = Number(data.stats.series_count).toLocaleString();
    }
    if (document.getElementById("stat-libs-count")) {
      document.getElementById("stat-libs-count").innerText = Number(data.stats.libraries_count).toLocaleString();
    }
    if (document.getElementById("stat-with-pages")) {
      document.getElementById("stat-with-pages").innerText = Number(data.stats.books_with_pages).toLocaleString();
    }
    if (document.getElementById("stat-missing-pages")) {
      document.getElementById("stat-missing-pages").innerText = Number(data.stats.books_missing_pages).toLocaleString();
    }

    // Update completion progress bar
    const total = data.stats.books_count || 0;
    const withPages = data.stats.books_with_pages || 0;
    const pct = total > 0 ? Math.round((withPages / total) * 100) : 0;
    const pbar = document.getElementById("pages-progress-bar");
    if (pbar) {
      pbar.style.width = pct + "%";
      const ptext = document.getElementById("pages-progress-text");
      if (ptext) ptext.innerText = pct + "% (" + withPages.toLocaleString() + " / " + total.toLocaleString() + " books)";
    }

    // Update Metadata Sync Job Info and Logs
    const syncStatus = data.sync_job;
    const syncCard = document.getElementById("sync-log-card");
    const syncBadge = document.getElementById("sync-status-badge");
    const syncLogCont = document.getElementById("sync-log-container");
    const syncBtn = document.getElementById("btn-sync");

    if (syncStatus) {
      if (syncStatus.is_running) {
        if (syncCard) syncCard.style.display = "block";
        if (syncBadge) {
          syncBadge.innerText = "SYNCING";
          syncBadge.className = "status-badge status-running";
        }
        if (syncBtn) {
          syncBtn.disabled = true;
          syncBtn.innerText = "Syncing...";
        }
      } else {
        if (syncBtn && syncBtn.disabled) {
          syncBtn.disabled = false;
          syncBtn.innerText = "🔄 Sync Metadata Now";
        }
        if (syncBadge) {
          if (syncStatus.error_message) {
            syncBadge.innerText = "FAILED";
            syncBadge.className = "status-badge status-failed";
          } else if (syncStatus.logs && syncStatus.logs.length > 0) {
            syncBadge.innerText = "COMPLETED";
            syncBadge.className = "status-badge status-completed";
          } else {
            syncBadge.innerText = "IDLE";
            syncBadge.className = "status-badge status-idle";
          }
        }
      }

      if (syncLogCont && syncStatus.logs && syncStatus.logs.length > 0) {
        if (syncCard) syncCard.style.display = "block";
        const newText = syncStatus.logs.join("\n");
        if (syncLogCont.innerText.trim() !== newText.trim()) {
          syncLogCont.innerText = newText;
          syncLogCont.scrollTop = syncLogCont.scrollHeight;
        }
      }
    }

    // Update User Read Status Sync Info and Logs
    const readSyncStatus = data.read_sync_job;
    const readSyncCard = document.getElementById("read-sync-log-card");
    const readSyncBadge = document.getElementById("read-sync-status-badge");
    const readSyncLogCont = document.getElementById("read-sync-log-container");
    const readSyncBtn = document.getElementById("btn-read-sync");

    if (readSyncStatus) {
      if (document.getElementById("read-sync-active-users")) {
        document.getElementById("read-sync-active-users").innerText = readSyncStatus.active_users_count || 0;
      }
      if (document.getElementById("read-sync-total-records")) {
        document.getElementById("read-sync-total-records").innerText = Number(readSyncStatus.total_synced_records || 0).toLocaleString();
      }
      if (document.getElementById("read-sync-last-time")) {
        document.getElementById("read-sync-last-time").innerText = readSyncStatus.last_sync_time || "Never";
      }

      if (readSyncStatus.is_running) {
        if (readSyncCard) readSyncCard.style.display = "block";
        if (readSyncBadge) {
          readSyncBadge.innerText = "SYNCING";
          readSyncBadge.className = "status-badge status-running";
        }
        if (readSyncBtn) {
          readSyncBtn.disabled = true;
          readSyncBtn.innerText = "Syncing...";
        }
      } else {
        if (readSyncBtn && readSyncBtn.disabled) {
          readSyncBtn.disabled = false;
          readSyncBtn.innerText = "📖 Sync Read Status Now";
        }
        if (readSyncBadge) {
          if (readSyncStatus.error_message) {
            readSyncBadge.innerText = "FAILED";
            readSyncBadge.className = "status-badge status-failed";
          } else if (readSyncStatus.logs && readSyncStatus.logs.length > 0) {
            readSyncBadge.innerText = "COMPLETED";
            readSyncBadge.className = "status-badge status-completed";
          } else {
            readSyncBadge.innerText = "IDLE";
            readSyncBadge.className = "status-badge status-idle";
          }
        }
      }

      if (readSyncLogCont && readSyncStatus.logs && readSyncStatus.logs.length > 0) {
        if (readSyncCard) readSyncCard.style.display = "block";
        const newReadText = readSyncStatus.logs.join("\n");
        if (readSyncLogCont.innerText.trim() !== newReadText.trim()) {
          readSyncLogCont.innerText = newReadText;
          readSyncLogCont.scrollTop = readSyncLogCont.scrollHeight;
        }
      }
    }

    // Update Active Calculation Job Info
    const jobBox = document.getElementById("active-job-box");
    const jobStatus = data.calc_job;
    if (jobBox && jobStatus) {
      if (jobStatus.is_running) {
        jobBox.style.display = "block";
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          badge.innerText = "RUNNING";
          badge.className = "status-badge status-running";
        }
        document.getElementById("job-processed").innerText = (jobStatus.processed_books || 0) + " / " + (jobStatus.total_books || 0);
        document.getElementById("job-updated").innerText = jobStatus.updated_books || 0;
        document.getElementById("job-errors").innerText = jobStatus.error_count || 0;
        document.getElementById("job-current-book").innerText = jobStatus.current_book || "-";
      } else if (jobStatus.job_id || jobStatus.total_books > 0) {
        jobBox.style.display = "block";
        const badge = document.getElementById("job-status-badge");
        if (badge) {
          if (jobStatus.error_message) {
            badge.innerText = "FAILED";
            badge.className = "status-badge status-failed";
          } else if (jobStatus.status === "stopped") {
            badge.innerText = "STOPPED";
            badge.className = "status-badge status-idle";
          } else {
            badge.innerText = "COMPLETED";
            badge.className = "status-badge status-completed";
          }
        }
        document.getElementById("job-processed").innerText = (jobStatus.processed_books || 0) + " / " + (jobStatus.total_books || 0);
        document.getElementById("job-updated").innerText = jobStatus.updated_books || 0;
        document.getElementById("job-errors").innerText = jobStatus.error_count || 0;
        document.getElementById("job-current-book").innerText = jobStatus.current_book || (jobStatus.error_message ? jobStatus.error_message : "Finished");
      }
    }

    // Update Users Count Pill if returned in status
    if (data.users && Array.isArray(data.users)) {
      const pill = document.getElementById("users-count-pill");
      if (pill) {
        pill.innerText = `${data.users.length} User${data.users.length === 1 ? '' : 's'}`;
      }
    }
  } catch (err) {
    console.debug("Status poll failed:", err);
  } finally {
    _isPolling = false;
  }
}

// User Management Actions
async function purgeUser(userId, username) {
  if (!confirm(`Are you sure you want to purge user "${username}"?\n\nThis will permanently remove all cached read progress, settings, and active sessions for this user from the proxy.`)) {
    return;
  }

  try {
    const res = await fetch(`/admin/api/users/${userId}/purge`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      alert(data.message || `User "${username}" has been purged.`);
      refreshUsers();
      pollStatus();
    } else {
      alert("Error: " + (data.detail || "Failed to purge user"));
    }
  } catch (err) {
    alert("Purge request failed: " + err);
  }
}

// Delegated handler for user action buttons (values come from data-* attributes, never inline JS)
document.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  const userId = Number(btn.dataset.userId);
  const username = btn.dataset.username || "";
  if (btn.dataset.action === "purge-user") {
    purgeUser(userId, username);
  } else if (btn.dataset.action === "sync-user") {
    syncSingleUser(userId, username, btn);
  }
});

async function syncSingleUser(userId, username, btn) {
  const originalText = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerText = "Syncing...";
  }

  try {
    const res = await fetch(`/admin/api/users/${userId}/sync`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      alert(data.message || `Read progress sync completed for "${username}".`);
      refreshUsers();
      pollStatus();
    } else {
      alert("Error: " + (data.detail || "Failed to sync user read progress"));
    }
  } catch (err) {
    alert("Sync request failed: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalText;
    }
  }
}

async function refreshUsers() {
  try {
    const res = await fetch("/admin/api/users");
    const data = await res.json();
    if (res.ok && data.users) {
      renderUsersTable(data.users);
    }
  } catch (err) {
    console.debug("Failed to refresh users:", err);
  }
}

function renderUsersTable(users) {
  const tbody = document.getElementById("users-table-body");
  const pill = document.getElementById("users-count-pill");
  if (pill) {
    pill.innerText = `${users.length} User${users.length === 1 ? '' : 's'}`;
  }
  if (!tbody) return;

  if (users.length === 0) {
    tbody.innerHTML = `
      <tr id="no-users-row">
        <td colspan="7" style="text-align: center; padding: 2.5rem 1rem; color: var(--color-text-muted); font-size: 0.875rem;">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.4; margin-bottom: 0.5rem; display: block; margin-left: auto; margin-right: auto;">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
            <circle cx="9" cy="7" r="4"></circle>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
            <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
          </svg>
          No reader clients have connected yet. Connect using <strong>Komic</strong>, <strong>Mihon</strong>, or <strong>Komga apps</strong> with your Grimmory credentials to register users here.
        </td>
      </tr>
    `;
    return;
  }

  let html = "";
  users.forEach(u => {
    const initial = (u.username || "U").charAt(0).toUpperCase();
    const onlineDot = u.is_online ? '<span title="Active in last 15 min" style="position: absolute; bottom: -1px; right: -1px; width: 9px; height: 9px; border-radius: 50%; background: #22c55e; border: 2px solid var(--color-card); box-shadow: 0 0 6px #22c55e;"></span>' : '';
    const roleBadge = u.is_admin 
      ? '<span class="user-role-badge role-admin">Administrator</span>' 
      : '<span class="user-role-badge role-user">User</span>';
    const completedText = u.completed_count > 0 
      ? `<span style="color: var(--color-success); font-size: 0.75rem; margin-left: 0.25rem;">(${u.completed_count} read)</span>` 
      : '';
    const syncBtn = u.has_token 
      ? `<button data-action="sync-user" data-user-id="${Number(u.id)}" data-username="${escapeHtml(u.username)}" class="btn btn-ghost btn-sm" title="Sync Grimmory read status for ${escapeHtml(u.username)}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
          </svg>
          <span>Sync</span>
        </button>` 
      : '';

    html += `
      <tr id="user-row-${u.id}">
        <td style="padding: 0.75rem;">
          <div style="display: flex; align-items: center; gap: 0.65rem;">
            <div style="position: relative; width: 32px; height: 32px; border-radius: 50%; background: var(--color-primary-light); display: flex; align-items: center; justify-content: center; color: var(--color-primary); font-weight: 700; font-size: 0.85rem; border: 1px solid rgba(249, 115, 22, 0.25);">
              ${initial}
              ${onlineDot}
            </div>
            <div>
              <div style="font-weight: 600; color: var(--color-text-strong); display: flex; align-items: center; gap: 0.35rem;">
                <span>${escapeHtml(u.username)}</span>
              </div>
              <div style="font-size: 0.72rem; color: var(--color-text-muted);">ID: ${u.id}</div>
            </div>
          </div>
        </td>
        <td style="padding: 0.75rem;">
          ${roleBadge}
        </td>
        <td style="padding: 0.75rem; color: var(--color-text); font-size: 0.85rem;">
          ${escapeHtml(u.libraries_display || 'All Libraries')}
        </td>
        <td style="padding: 0.75rem; font-size: 0.85rem;">
          <strong style="color: var(--color-text-strong);">${u.progress_count || 0}</strong>
          <span style="color: var(--color-text-muted);">books</span>
          ${completedText}
        </td>
        <td style="padding: 0.75rem; color: var(--color-text-muted); font-size: 0.825rem;">
          ${escapeHtml(u.last_connected_at || '-')}
        </td>
        <td style="padding: 0.75rem; color: var(--color-text-muted); font-size: 0.825rem;">
          ${escapeHtml(u.last_sync_progress_at || 'Never')}
        </td>
        <td style="padding: 0.75rem; text-align: right;">
          <div style="display: flex; gap: 0.4rem; justify-content: flex-end; align-items: center;">
            ${syncBtn}
            <button data-action="purge-user" data-user-id="${Number(u.id)}" data-username="${escapeHtml(u.username)}" class="btn btn-danger-ghost btn-sm" title="Purge user and clear cached read progress for ${escapeHtml(u.username)}">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
              <span>Purge</span>
            </button>
          </div>
        </td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Start polling every 3 seconds only on the admin dashboard
function initPolling() {
  if (document.getElementById("btn-sync") || document.getElementById("stat-books-count") || document.getElementById("active-job-box")) {
    setInterval(pollStatus, 3000);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initPolling);
} else {
  initPolling();
}

