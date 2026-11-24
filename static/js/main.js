document.addEventListener("DOMContentLoaded", () => {
  const dismissFlash = (flash) => {
    flash.classList.add("is-dismissing");
    window.setTimeout(() => {
      flash.style.display = "none";
      const stack = flash.closest(".flash-stack");
      if (stack && !stack.querySelector(".flash:not(.is-dismissing)")) {
        stack.style.display = "none";
      }
    }, 350);
  };

  document.querySelectorAll(".flash").forEach((flash) => {
    const closeButton = flash.querySelector(".flash-dismiss");
    if (closeButton) closeButton.addEventListener("click", () => dismissFlash(flash));
    window.setTimeout(() => dismissFlash(flash), 3500);
  });

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

  document.querySelectorAll("[data-tab-target]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabList = tab.closest('[role="tablist"]');
      if (!tabList) return;
      tabList.querySelectorAll('[role="tab"]').forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      tabList.parentElement
        .querySelectorAll(".tab-panel")
        .forEach((panel) => {
          panel.hidden = panel.id !== tab.dataset.tabTarget;
        });
    });
  });

  const groupDataNode = document.getElementById("group-chart-data");
  if (groupDataNode && window.Chart) {
    const chartData = JSON.parse(groupDataNode.textContent);
    const monthlyData = chartData.monthly || { labels: [], values: [] };
    const categoriesData = chartData.categories || { labels: [], values: [] };
    const monthlyCanvas = document.querySelector('[data-chart="monthly"]');
    const categoryCanvas = document.querySelector('[data-chart="categories"]');
    const palette = ["#0F2C59", "#1B3B6F", "#C59B27", "#475569"];
    const numberFormat = new Intl.NumberFormat("en-US");

    if (monthlyCanvas) {
      new Chart(monthlyCanvas, {
          type: "line",
          data: {
            labels: monthlyData.labels,
            datasets: [{
              label: chartData.metric_label || "Registros",
              data: monthlyData.values,
              borderColor: palette[0],
              backgroundColor: "rgba(15, 44, 89, 0.08)",
              borderWidth: 2,
              pointRadius: 3,
              pointBackgroundColor: palette[2],
              fill: true,
              tension: 0.25,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (context) => `${context.dataset.label}: ${numberFormat.format(context.parsed.y)}`,
                },
              },
            },
            scales: {
              x: { grid: { display: false } },
              y: { beginAtZero: true, ticks: { callback: (value) => numberFormat.format(value) } },
            },
          },
      });
    }

    if (categoryCanvas) {
      new Chart(categoryCanvas, {
          type: "bar",
          data: {
            labels: categoriesData.labels,
            datasets: [{
              label: chartData.category_label || "Categorías",
              data: categoriesData.values,
              backgroundColor: categoriesData.labels.map((_, index) => palette[index % palette.length]),
              borderWidth: 0,
              borderRadius: 2,
            }],
          },
          options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (context) => numberFormat.format(context.parsed.x),
                },
              },
            },
            scales: {
              x: { beginAtZero: true, ticks: { callback: (value) => numberFormat.format(value) } },
              y: { grid: { display: false } },
            },
          },
      });
    }
  }

  const centralChartNode = document.getElementById("central-chart-data");
  const centralCanvas = document.querySelector('[data-chart="central-activity"]');
  if (centralChartNode && centralCanvas && window.Chart) {
    const chartData = JSON.parse(centralChartNode.textContent);
    const labels = chartData.labels || [];
    const values = chartData.values || [];
    const numberFormat = new Intl.NumberFormat("en-US");
    new Chart(centralCanvas, {
        type: "bar",
        data: {
          labels,
          datasets: [{
            label: "Filas procesadas",
            data: values,
            backgroundColor: labels.map((_, index) => ["#0F2C59", "#1B3B6F", "#C59B27", "#475569"][index % 4]),
            borderWidth: 0,
            borderRadius: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { callback: (value) => numberFormat.format(value) } },
          },
        },
    });
  }

  const etlChartNode = document.getElementById("etl-monitor-chart-data");
  const etlCanvas = document.querySelector('[data-chart="etl-monitor"]');
  if (etlChartNode && etlCanvas && window.Chart) {
    const chartData = JSON.parse(etlChartNode.textContent);
    const numberFormat = new Intl.NumberFormat("en-US");
    new Chart(etlCanvas, {
      type: "bar",
      data: {
        labels: chartData.labels || [],
        datasets: [
          {
            label: "Recibidos en buzón",
            data: chartData.received || [],
            backgroundColor: "#0F2C59",
            borderWidth: 0,
            borderRadius: 2,
          },
          {
            label: "Enviados al DW",
            data: chartData.sent || [],
            backgroundColor: "#C59B27",
            borderWidth: 0,
            borderRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { callback: (value) => numberFormat.format(value) },
          },
        },
      },
    });
  }
});
