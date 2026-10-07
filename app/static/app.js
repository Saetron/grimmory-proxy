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
  const btn = event.target;
  const originalText = btn.innerText;
  btn.disabled = true;
  btn.innerText = "Syncing...";

  try {
    const res = await fetch("/admin/api/sync", { method: "POST" });
    const data = await res.json();
    if (!res.ok) alert("Error: " + (data.detail || "Failed to trigger sync"));
  } catch (err) {
    alert("Request failed: " + err);
  } finally {
    btn.disabled = false;
    btn.innerText = originalText;
    pollStatus();
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
