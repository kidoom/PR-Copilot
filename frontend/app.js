const API_URL = "http://localhost:8000/api/health";

const statusEl = document.getElementById("status");
const checkBtn = document.getElementById("check-btn");

async function checkHealth() {
  statusEl.textContent = "Checking backend connection...";
  statusEl.className = "loading";

  try {
    const res = await fetch(API_URL);
    const data = await res.json();
    statusEl.textContent = `Backend is running: ${data.service} — status ${data.status}`;
    statusEl.className = "success";
  } catch (err) {
    statusEl.textContent = `Backend unreachable: ${err.message}`;
    statusEl.className = "failure";
  }
}

checkBtn.addEventListener("click", checkHealth);

// Auto-check on page load
checkHealth();
