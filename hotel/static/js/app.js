/* Hotel Management System - small progressive enhancements.
   The pages still work with plain forms; this adds convenience. */
(function () {
  "use strict";

  /* ---- Mobile menu ---------------------------------------------------- */
  var toggle = document.getElementById("menu-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    function setOpen(open) {
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    toggle.addEventListener("click", function (event) {
      event.stopPropagation();
      setOpen(!document.body.classList.contains("nav-open"));
    });
    document.addEventListener("click", function (event) {
      if (document.body.classList.contains("nav-open") && !nav.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setOpen(false);
    });
  }

  /* ---- Confirm before risky actions (data-confirm="message") ---------- */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) event.preventDefault();
  });

  /* ---- Print button on the bill --------------------------------------- */
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("[data-print]")) window.print();
  });

  /* ---- Auto-submit selects marked data-autosubmit ---------------------- */
  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () { select.form.submit(); });
  });

  /* ---- Booking form: refresh room availability when dates change ------ */
  var form = document.getElementById("booking-form");
  if (!form) return;

  var api = form.getAttribute("data-api");
  var list = document.getElementById("avail-list");
  var summary = document.getElementById("avail-summary");
  var checkIn = document.getElementById("check_in");
  var checkOut = document.getElementById("check_out");
  var guests = document.getElementById("num_guests");
  var requestId = 0;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function selectedRoom() {
    var checked = list.querySelector("input[name=room_id]:checked");
    return checked ? checked.value : "";
  }

  function renderRoom(room, keep) {
    var li = document.createElement("li");
    var label = el("label", "avail-item " + (room.available ? "is-free" : "is-taken"));
    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "room_id";
    radio.value = String(room.id);
    radio.required = true;
    radio.disabled = !room.available;
    radio.checked = room.available && String(room.id) === keep;

    var main = el("span");
    main.appendChild(el("span", "avail-main", "Room " + room.room_number));
    main.appendChild(document.createTextNode(" "));
    main.appendChild(el("span", "muted", room.room_type));
    main.appendChild(document.createElement("br"));
    main.appendChild(el("span", "avail-sub", "Sleeps " + room.capacity + ", " + room.rate + " per night"));

    var side = el("span", "avail-side");
    side.appendChild(el("span", "avail-state", room.available ? "Available" : "Not available"));
    side.appendChild(document.createElement("br"));
    side.appendChild(el("span", "avail-price", room.available ? "Estimated total " + room.estimate_total : room.reason));

    label.appendChild(radio);
    label.appendChild(main);
    label.appendChild(side);
    li.appendChild(label);
    return li;
  }

  function refresh() {
    if (!checkIn.value || !checkOut.value) return;
    var id = ++requestId;
    var params = new URLSearchParams({
      check_in: checkIn.value,
      check_out: checkOut.value,
      num_guests: guests.value || ""
    });
    summary.textContent = "Checking availability...";
    fetch(api + "?" + params.toString(), { headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (id !== requestId) return; // a newer request is already running
        var keep = selectedRoom();
        list.textContent = "";
        if (!data.ok) {
          summary.textContent = data.error;
          return;
        }
        var free = data.rooms.filter(function (r) { return r.available; }).length;
        summary.textContent = data.rooms.length
          ? free + " of " + data.rooms.length + " room" + (data.rooms.length === 1 ? "" : "s") +
            " free for " + data.nights + " night" + (data.nights === 1 ? "" : "s") + "."
          : "There are no rooms yet. Add a room first.";
        data.rooms.forEach(function (room) { list.appendChild(renderRoom(room, keep)); });
      })
      .catch(function () {
        if (id === requestId) summary.textContent = "Could not check availability. You can still submit; the server checks again.";
      });
  }

  checkOut.addEventListener("change", refresh);
  guests.addEventListener("change", refresh);

  /* Moving check-in: keep check-out after it (if needed), then refresh once. */
  checkIn.addEventListener("change", function () {
    if (checkIn.value && (!checkOut.value || checkOut.value <= checkIn.value)) {
      var d = new Date(checkIn.value + "T00:00:00");
      d.setDate(d.getDate() + 1);
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      checkOut.value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }
    refresh();
  });
})();
