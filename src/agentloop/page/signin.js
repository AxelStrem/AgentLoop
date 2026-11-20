// The sign-in form: the token is sent once, as JSON, and the server answers with the cookie the
// page's requests carry from then on (item serve-lan).
"use strict";

document.getElementById("signin").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.getElementById("message");
  const button = event.target.querySelector("button");
  message.hidden = true;
  button.disabled = true;
  try {
    const answer = await fetch("/api/signin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: document.getElementById("token").value.trim() }),
      credentials: "same-origin",
    });
    if (answer.ok) {
      location.replace("/");
      return;
    }
    const data = await answer.json().catch(() => ({}));
    message.textContent = data.error || `The server answered ${answer.status}.`;
  } catch (error) {
    message.textContent = "The server cannot be reached.";
  } finally {
    button.disabled = false;
  }
  message.hidden = false;
});
