"use strict";
(function () {
  const config = document.getElementById("tm-run-config");
  if (!config) return;
  const observerId = JSON.parse(config.textContent).observerId;
  document.addEventListener("click", async event => {
    const button = event.target.closest?.("[data-final-report-generate]");
    if (!button || button.disabled) return;
    const slot = button.closest("[data-final-report-entry]");
    const errorText = slot.querySelector("[data-final-report-error]");
    const previousLabel = button.textContent;
    slot.dataset.reportSubmitting = "1";
    button.disabled = true;
    button.textContent = "启动中…";
    errorText.hidden = true;
    try {
      const reply = await fetch(`/api/run/${encodeURIComponent(observerId)}/final-report/generate`, {
        method: "POST", headers: {"X-TM-Operator-Intent": "1"}
      });
      const result = await reply.json();
      if (!reply.ok || !result.ok) throw new Error(result.error || "总报告生成未能启动，请重试");
      if (["queued", "running"].includes(result.state)) slot.dataset.reportStarting = "1";
      button.textContent = "生成中…";
      slot.querySelector(".tm-final-report-status").textContent = "已提交";
      slot.querySelector(".tm-final-report-title").textContent = "总报告已提交生成，完成后可在此阅读";
    } catch (error) {
      errorText.textContent = error.message || "网络连接失败，请重试";
      errorText.hidden = false;
      button.disabled = false;
      button.textContent = previousLabel;
    } finally {
      delete slot.dataset.reportSubmitting;
    }
  });
})();
