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
    if (logCont) logCont.innerHTML = '<span style="color: var(--text-muted);">Log cleared.</span>';
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
    if (logCont) logCont.innerHTML = '<span style="color: var(--text-muted);">Log cleared.</span>';
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
  } catch (err) {
    console.debug("Status poll failed:", err);
  }
}

// Start polling every 3 seconds
setInterval(pollStatus, 3000);
