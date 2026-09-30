(function () {
  "use strict";

  var map = L.map("map").setView([39.8283, -98.5795], 4);

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  var routeLayerGroup = L.layerGroup().addTo(map);

  var form = document.getElementById("route-form");
  var startInput = document.getElementById("start-input");
  var finishInput = document.getElementById("finish-input");
  var submitBtn = document.getElementById("submit-btn");
  var loadingBox = document.getElementById("loading");
  var errorBox = document.getElementById("error-box");
  var summarySection = document.getElementById("summary");

  var summaryDistance = document.getElementById("summary-distance");
  var summaryStops = document.getElementById("summary-stops");
  var summaryGallonsConsumed = document.getElementById("summary-gallons-consumed");
  var summaryGallons = document.getElementById("summary-gallons");
  var summaryInitialCost = document.getElementById("summary-initial-cost");
  var summaryCost = document.getElementById("summary-cost");
  var stopsList = document.getElementById("stops-list");

  function setLoading(isLoading) {
    if (isLoading) {
      submitBtn.disabled = true;
      loadingBox.style.display = "block";
      errorBox.style.display = "none";
    } else {
      submitBtn.disabled = false;
      loadingBox.style.display = "none";
    }
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = "block";
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.style.display = "none";
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearError();

    var startVal = startInput.value.trim();
    var finishVal = finishInput.value.trim();

    if (!startVal || !finishVal) {
      showError("Please provide both start and finish locations.");
      return;
    }

    setLoading(true);

    fetch("/api/v1/route/plan/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        start: startVal,
        finish: finishVal,
      }),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (res) {
        setLoading(false);

        if (!res.ok) {
          var errMsg = res.data && res.data.error ? res.data.error : "Request failed.";
          showError(errMsg);
          return;
        }

        renderPlan(res.data);
      })
      .catch(function (err) {
        setLoading(false);
        showError("Network error: " + err.message);
      });
  });

  function renderPlan(data) {
    routeLayerGroup.clearLayers();

    summaryDistance.textContent = data.total_distance_miles.toFixed(1) + " mi";
    summaryStops.textContent = data.fuel_stops ? data.fuel_stops.length : 0;
    summaryGallonsConsumed.textContent = data.total_gallons_consumed.toFixed(1) + " gal";
    summaryGallons.textContent = (data.total_gallons_purchased || 0).toFixed(1) + " gal";
    summaryInitialCost.textContent = "$" + parseFloat(data.initial_fuel_cost || 0).toFixed(2);
    summaryCost.textContent = "$" + parseFloat(data.total_fuel_cost || 0).toFixed(2);
    summarySection.style.display = "flex";

    // Populate stops list in sidebar
    stopsList.innerHTML = "";
    if (data.fuel_stops && data.fuel_stops.length > 0) {
      data.fuel_stops.forEach(function (stop) {
        var li = document.createElement("li");
        li.className = "stop-item";
        li.innerHTML =
          "<div class='stop-title'>" +
          stop.name +
          " (" +
          stop.city +
          ", " +
          stop.state +
          ")</div>" +
          "<div class='stop-meta'>" +
          "Mile " +
          stop.distance_along_route_miles +
          " &bull; " +
          stop.gallons_purchased +
          " gal @ $" +
          stop.price_per_gallon +
          " = $" +
          stop.cost +
          "</div>";
        stopsList.appendChild(li);
      });
    } else {
      var li = document.createElement("li");
      li.className = "stop-item";
      li.style.listStyleType = "none";
      li.textContent = "No stops needed within initial fuel range.";
      stopsList.appendChild(li);
    }

    // Render GeoJSON route on map
    var bounds = L.latLngBounds([]);

    if (data.route && data.route.features) {
      data.route.features.forEach(function (feature) {
        if (feature.geometry.type === "LineString") {
          var latLngs = feature.geometry.coordinates.map(function (c) {
            return [c[1], c[0]];
          });
          var line = L.polyline(latLngs, {
            color: "#0969da",
            weight: 4,
            opacity: 0.85,
          }).addTo(routeLayerGroup);
          bounds.extend(line.getBounds());
        } else if (feature.geometry.type === "Point") {
          var ptCoords = [feature.geometry.coordinates[1], feature.geometry.coordinates[0]];
          bounds.extend(ptCoords);
          var props = feature.properties;

          if (props.type === "start") {
            var startIcon = L.divIcon({
              className: "",
              html: "<div class='terminal-marker'>START</div>",
              iconSize: [50, 20],
              iconAnchor: [25, 10],
            });
            L.marker(ptCoords, { icon: startIcon })
              .bindPopup("<strong>Start</strong><br>Mile: 0.0")
              .addTo(routeLayerGroup);
          } else if (props.type === "finish") {
            var finishIcon = L.divIcon({
              className: "",
              html: "<div class='terminal-marker'>FINISH</div>",
              iconSize: [55, 20],
              iconAnchor: [27, 10],
            });
            L.marker(ptCoords, { icon: finishIcon })
              .bindPopup("<strong>Finish</strong><br>Mile: " + props.mile + " mi")
              .addTo(routeLayerGroup);
          }
        }
      });
    }

    // Add numbered markers for fuel stops
    if (data.fuel_stops && data.fuel_stops.length > 0) {
      data.fuel_stops.forEach(function (stop, idx) {
        var num = idx + 1;
        var stopCoords = [stop.latitude, stop.longitude];
        bounds.extend(stopCoords);

        var numIcon = L.divIcon({
          className: "",
          html: "<div class='fuel-stop-marker'>" + num + "</div>",
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        });

        var popupContent =
          "<strong>Stop " +
          num +
          ": " +
          stop.name +
          "</strong><br>" +
          stop.city +
          ", " +
          stop.state +
          "<br>" +
          "<strong>Mile:</strong> " +
          stop.distance_along_route_miles +
          " mi<br>" +
          "<strong>Price:</strong> $" +
          stop.price_per_gallon +
          " / gal<br>" +
          "<strong>Purchased:</strong> " +
          stop.gallons_purchased +
          " gal<br>" +
          "<strong>Cost:</strong> $" +
          stop.cost +
          "<br>" +
          "<strong>Arrival Fuel:</strong> " +
          stop.fuel_remaining_on_arrival +
          " gal";

        L.marker(stopCoords, { icon: numIcon })
          .bindPopup(popupContent)
          .addTo(routeLayerGroup);
      });
    }

    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  }
})();
