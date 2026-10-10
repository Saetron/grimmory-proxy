// Admin WebUI Interactive Controller

async function triggerPageCalc(missingOnly) {
  const btn = event.target;
  const originalText = btn.innerText;
  btn.disabled = true;
  btn.innerText = "Starting...";

  try {
    const res = await fetch("/admin/api/calculate-pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ missing_only: missingOnly })
    });
    const data = await res.json();
    if (!res.ok) {
      alert("Error: " + (data.detail || "Failed to trigger page calculation"));
    }
  } catch (err) {
    alert("Request failed: " + err);
  } finally {
    btn.disabled = false;
    btn.innerText = originalText;
    pollStatus();
  }
}

async function stopPageCalc() {
  try {
    const res = await fetch("/admin/api/stop-calculation", { method: "POST" });
    const data = await res.json();
    if (!res.ok) alert(data.detail || "Failed to stop job");
  } catch (err) {
    alert("Request failed: " + err);
  } finally {
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

async function pollStatus() {
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
        document.getElementById("job-status-badge").innerText = "RUNNING";
        document.getElementById("job-status-badge").className = "status-badge status-running";
        document.getElementById("job-processed").innerText = jobStatus.processed_books + " / " + jobStatus.total_books;
        document.getElementById("job-updated").innerText = jobStatus.updated_books;
        document.getElementById("job-errors").innerText = jobStatus.error_count;
        document.getElementById("job-current-book").innerText = jobStatus.current_book || "-";
      } else if (jobStatus.total_books > 0) {
        jobBox.style.display = "block";
        document.getElementById("job-status-badge").innerText = "COMPLETED";
        document.getElementById("job-status-badge").className = "status-badge status-completed";
        document.getElementById("job-processed").innerText = jobStatus.processed_books + " / " + jobStatus.total_books;
        document.getElementById("job-updated").innerText = jobStatus.updated_books;
        document.getElementById("job-errors").innerText = jobStatus.error_count;
        document.getElementById("job-current-book").innerText = "Finished";
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

async function syncSingleUser(userId, username) {
  const btn = window.event && window.event.target ? window.event.target.closest("button") : null;
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
      ? `<button onclick="syncSingleUser(${u.id}, '${escapeHtml(u.username)}')" class="btn btn-ghost btn-sm" title="Sync Grimmory read status for ${escapeHtml(u.username)}">
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
          ${u.last_connected_at || '-'}
        </td>
        <td style="padding: 0.75rem; color: var(--color-text-muted); font-size: 0.825rem;">
          ${u.last_sync_progress_at || 'Never'}
        </td>
        <td style="padding: 0.75rem; text-align: right;">
          <div style="display: flex; gap: 0.4rem; justify-content: flex-end; align-items: center;">
            ${syncBtn}
            <button onclick="purgeUser(${u.id}, '${escapeHtml(u.username)}')" class="btn btn-danger-ghost btn-sm" title="Purge user and clear cached read progress for ${escapeHtml(u.username)}">
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

// Start polling every 3 seconds
setInterval(pollStatus, 3000);

