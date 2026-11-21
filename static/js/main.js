document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll(".quick-filter").forEach((input) => {
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      const rows = document.querySelectorAll(".filterable-table tbody tr");
      rows.forEach((row) => {
        row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
      });
    });
  });
});
