let map;
let markersLayer;
let spotsLayer;
let currentLat = 1.3521;
let currentLng = 103.8198;
let currentAbort = null;
const PAGE_SIZE = 20;
let michelinFilterActive = false;
let vegFilterActive = false;
let loadedPages = {};

function safePhotoUrl(url) {
  if (!url) return '';
  if (url.startsWith('//')) return 'https:' + url;
  if (url.startsWith('http://')) return url.replace('http://', 'https://');
  return url;
}

function getStatusBadgeClass(status) {
  if (!status || status === 'Existing') return '';
  const s = status.toLowerCase();
  if (s.includes('reno') || s.includes('upgrad')) return 'badge bg-warning text-dark';
  if (s.includes('closed') || s.includes('demolish') || s.includes('permanent')) return 'badge bg-danger';
  return 'badge bg-secondary';
}

function getMarkerColor(status) {
  if (!status || status === 'Existing') return '#E65100';
  const s = status.toLowerCase();
  if (s.includes('reno') || s.includes('upgrad')) return '#f9a825';
  if (s.includes('closed') || s.includes('demolish') || s.includes('permanent')) return '#c62828';
  return '#757575';
}

function getDistanceClass(d) {
  if (!d) return '';
  if (d < 0.5) return 'distance-near';
  if (d < 2) return 'distance-mid';
  return '';
}

let _lastLocatedLat = null;
let _lastLocatedLng = null;

function initMap(centres, lat, lng) {
  const ov = document.getElementById('map-loading');
  if (ov) ov.style.display = 'none';

  if (!map) {
    map = L.map('map').setView([lat || currentLat, lng || currentLng], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'
    }).addTo(map);
    const locateControl = L.control.locate({
      position: 'topright',
      strings: {title: 'Find my location'},
      watch: false
    }).addTo(map);

    map.on('locationfound', function(e) {
      const lat = +e.latlng.lat.toFixed(4);
      const lng = +e.latlng.lng.toFixed(4);
      if (_lastLocatedLat === lat && _lastLocatedLng === lng) return;
      _lastLocatedLat = lat;
      _lastLocatedLng = lng;
      currentLat = lat;
      currentLng = lng;
      const latInput = document.getElementById('user-lat');
      const lngInput = document.getElementById('user-lng');
      if (latInput) latInput.value = lat;
      if (lngInput) lngInput.value = lng;
      const sort = document.getElementById('sort-by');
      if (sort) sort.value = 'distance';
      performSearch();
    });

    map.on('moveend', function() {
      // Ignore programmatic moves (fitBounds/setView) to prevent loops
      if (map._ignoreNextMoveend) { map._ignoreNextMoveend = false; return; }
      const center = map.getCenter();
      currentLat = center.lat;
      currentLng = center.lng;
      const latInput = document.getElementById('user-lat');
      const lngInput = document.getElementById('user-lng');
      if (latInput) latInput.value = center.lat;
      if (lngInput) lngInput.value = center.lng;
    });
  }

  if (markersLayer) {
    // Don't destroy markers if a popup is currently open
    let popupOpen = false;
    if (map) {
      map.eachLayer(l => { if (l instanceof L.Marker && l.getPopup && l.isPopupOpen()) popupOpen = true; });
    }
    if (!popupOpen) map.removeLayer(markersLayer);
  }
  // Clustered markers layer — groups nearby centres when zoomed out (declutter)
  if (!markersLayer || !map.hasLayer(markersLayer)) {
    markersLayer = L.markerClusterGroup({
      maxClusterRadius: 48,
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      iconCreateFunction: (cluster) => {
        const count = cluster.getChildCount();
        return L.divIcon({
          html: `<div style="background:linear-gradient(135deg,#E65100,#FF833A);color:#fff;border-radius:50%;width:38px;height:38px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;box-shadow:0 3px 12px rgba(230,81,0,.4);border:2px solid rgba(255,255,255,.7);">${count}</div>`,
          iconSize: [38, 38], iconAnchor: [19, 19], className: ''
        });
      }
    }).addTo(map);
  }
  // Non-hawker spots layer — render once, persistent
  if (!spotsLayer || !map.hasLayer(spotsLayer)) {
    spotsLayer = L.layerGroup().addTo(map);
    loadSpots();
  }
  if (!centres || centres.length === 0) {
    const overlay = document.getElementById('map-loading');
    if (overlay) overlay.style.display = 'none';
    return;
  }

  centres.forEach((c) => {
    const color = getMarkerColor(c.status);
    const icon = L.divIcon({
      html: `<div style="background:${color};color:#fff;border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center;font-size:17px;box-shadow:0 2px 8px rgba(0,0,0,.3);border:2px solid rgba(255,255,255,.5);transition:transform .2s;">🍜</div>`,
      iconSize: [34, 34], iconAnchor: [17, 17], className: ''
    });
    L.marker([c.lat, c.lng], {icon})
      .bindPopup(`<div style="min-width:210px;font-family:'Inter',sans-serif;">
        ${c.photo_url 
          ? `<div style="width:100%;height:110px;border-radius:10px;overflow:hidden;margin-bottom:8px;background:#f5f0eb;">
              <img src="${safePhotoUrl(c.photo_url)}" style="width:100%;height:100%;object-fit:cover;" 
                   onerror="this.outerHTML='<div style=\\'width:100%;height:100%;background:linear-gradient(135deg,#f5f0eb,#e8d5c0);display:flex;align-items:center;justify-content:center;\\'><span style=\\'font-size:2rem;\\'>🍜</span></div>'">
             </div>`
          : `<div style="width:100%;height:110px;border-radius:10px;margin-bottom:8px;background:linear-gradient(135deg,#f5f0eb,#e8d5c0);display:flex;align-items:center;justify-content:center;"><span style="font-size:2.5rem;">🍜</span></div>`}
        <b style="font-size:1.05rem;color:#1a1a1a;">${c.michelin_count > 0 ? '<img src="/static/img/bib-gourmand-icon.png" style="width:1.1em;height:1em;vertical-align:middle;" alt="BG"> ' : ''}${c.name}</b><br>
        <small style="color:#888;">${c.address || 'Singapore'}</small><br>
        <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
          ${c.status && c.status !== 'Existing' ? `<span class="${getStatusBadgeClass(c.status)}" style="font-size:.65rem;">${c.status}</span>` : ''}
          <span class="badge rounded-pill" style="font-size:.6rem;background:#e8e0d8;color:#555;font-weight:500;"><i class="bi bi-shop" style="font-size:0.65rem;"></i> ${c.stalls} stalls</span>
        </div>
        <div class="mt-2"><a href="/centre/${c.id}" style="color:#E65100;font-size:.85rem;font-weight:600;text-decoration:none;">View details →</a></div>
        <div style="margin-top:6px;text-align:center;font-size:.65rem;color:#bbb;border-top:1px solid #eee;padding-top:6px;">✕ tap outside to close</div>
      </div>`)
      .addTo(markersLayer);
  });

  if (centres.length > 0) {
    try {
      if (lat && lng) {
        L.circleMarker([lat, lng], {
          radius: 8, color: '#2962FF', fillColor: '#2962FF',
          fillOpacity: 0.3, weight: 3, opacity: 0.8,
          className: 'user-location-marker'
        }).addTo(markersLayer).bindPopup('📍 You are here');

        if (map._zoomToUser !== true) {
          map._ignoreNextMoveend = true;
          map.setView([lat, lng], 15);
          map._zoomToUser = true;
          const checkVisible = () => {
            const halfIcon = 20;
            const size = map.getSize();
            const fullyVisible = centres.some(c => {
              const pt = map.latLngToContainerPoint([c.lat, c.lng]);
              return pt.x > halfIcon && pt.x < size.x - halfIcon &&
                     pt.y > halfIcon && pt.y < size.y - halfIcon;
            });
            if (!fullyVisible && map.getZoom() > 11) {
              map.zoomOut(1, {animate: false});
              setTimeout(checkVisible, 200);
            }
          };
          setTimeout(checkVisible, 600);
        }
      } else {
        const group = L.featureGroup(centres.map(c => L.marker([c.lat, c.lng])));
        map._ignoreNextMoveend = true;
        map.fitBounds(group.getBounds().pad(0.1));
      }
    } catch(e) { /* single point */ }
  }
}

function renderCard(c, idx) {
  const statusClass = getStatusBadgeClass(c.status);
  const thumbHtml = c.photo_url
    ? `<img src="${safePhotoUrl(c.photo_url)}" alt="${c.name}" loading="lazy" style="width:64px;height:64px;border-radius:8px;object-fit:cover;" onerror="this.outerHTML='<div class=\\'thumb-fallback\\'><span>🍜</span></div>'">`
    : `<div class="thumb-fallback"><span>🍜</span></div>`;
  return `
    <a href="/centre/${c.id}" class="result-card" id="card-${idx}">
      <div class="d-flex justify-content-between align-items-start">
        <div class="flex-grow-1">
          <div class="name">${c.michelin_count > 0 ? '<img src="/static/img/bib-gourmand-icon.png" style="width:1.1em;height:1em;vertical-align:middle;" alt="BG"> ' : ''}${c.name}</div>
          <div class="address"><i class="bi bi-geo-alt" style="font-size:0.8rem;color:#999;"></i> ${c.address || 'Singapore'}</div>
          <div class="mt-2 d-flex gap-2 flex-wrap align-items-center">
            ${c.status && c.status !== 'Existing' ? `<span class="badge rounded-pill ${statusClass}" style="font-size:.7rem;font-weight:600;">${c.status}</span>` : ''}
            <span class="distance-badge"><i class="bi bi-shop" style="font-size:0.65rem;"></i> ${c.stalls} cooked food stalls</span>
            ${c.michelin_count > 0 ? `<span class="distance-badge" style="background:#fff1e0;color:#b8860b;border-color:#daa520;"><img src="/static/img/bib-gourmand-icon.png" style="width:.85em;height:.85em;vertical-align:middle;" alt="BG"> ${c.michelin_count} Bib Gourmand</span>` : ''}
            ${c._dist ? `<span class="distance-badge"><i class="bi bi-pin-map" style="font-size:0.65rem;"></i> ${c._dist} km away</span>` : ''}
          </div>
        </div>
        <div class="text-end flex-shrink-0 ms-3">
          ${thumbHtml}
        </div>
      </div>
    </a>`;
}

document.addEventListener('DOMContentLoaded', () => {

  const locBtn = document.getElementById('loc-btn');
  if (locBtn) {
    locBtn.addEventListener('click', () => {
      if (!navigator.geolocation) { alert('Geolocation not available'); return; }
      locBtn.innerHTML = '<span class="loading-spinner"></span>';
      locBtn.disabled = true;
      navigator.geolocation.getCurrentPosition(pos => {
        currentLat = pos.coords.latitude;
        currentLng = pos.coords.longitude;
        document.getElementById('user-lat').value = currentLat;
        document.getElementById('user-lng').value = currentLng;
        document.getElementById('sort-by').value = 'distance';
        setLocationCookie(currentLat, currentLng);
        locBtn.innerHTML = '<i class="bi bi-geo-alt-fill"></i>';
        locBtn.disabled = false;
        performSearch();
      }, () => { 
        locBtn.innerHTML = '<i class="bi bi-geo-alt-fill"></i>';
        locBtn.disabled = false;
        alert('Could not get your location. Please enable location access.'); 
      }, {enableHighAccuracy: true, timeout: 10000});
    });
  }

  const clearBtn = document.getElementById('clear-filters');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      document.getElementById('filter-district').value = '';
      document.getElementById('sort-by').value = 'relevance';
      document.getElementById('user-lat').value = '';
      document.getElementById('user-lng').value = '';
      performSearch();
    });
  }

  document.querySelectorAll('#filter-district, #sort-by').forEach(el => {
    el.addEventListener('change', performSearch);
  });

  // Try location cookie
  const cookieLoc = getLocationCookie();
  if (cookieLoc) {
    currentLat = cookieLoc.lat;
    currentLng = cookieLoc.lng;
    document.getElementById('user-lat').value = currentLat;
    document.getElementById('user-lng').value = currentLng;
    document.getElementById('sort-by').value = 'distance';
    performSearch();
    return;
  }
  
  // Render immediately with default view, then refine with geolocation
  performSearch();
  
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      currentLat = pos.coords.latitude;
      currentLng = pos.coords.longitude;
      document.getElementById('user-lat').value = currentLat;
      document.getElementById('user-lng').value = currentLng;
      document.getElementById('sort-by').value = 'distance';
      performSearch();
      setLocationCookie(currentLat, currentLng);
      const locBtn = document.getElementById('loc-btn');
      if (locBtn) locBtn.innerHTML = '<i class="bi bi-geo-alt-fill" style="color:var(--primary);"></i>';
    }, () => {
      if (!cookieLoc) performSearch();
    }, {enableHighAccuracy: true, timeout: 8000});
  }

  const michelinLegend = document.getElementById('michelin-legend');
  if (michelinLegend && !michelinLegend._clickBound) {
    michelinLegend._clickBound = true;
    michelinLegend.addEventListener('click', () => {
      michelinFilterActive = !michelinFilterActive;
      michelinLegend.classList.toggle('active-filter', michelinFilterActive);
      performSearch();
    });
  }
  const vegLegend = document.getElementById('veg-legend');
  if (vegLegend && !vegLegend._clickBound) {
    vegLegend._clickBound = true;
    vegLegend.addEventListener('click', () => {
      setVegFilter(!vegFilterActive);
    });
  }
  const vegToggle = document.getElementById('veg-toggle');
  if (vegToggle && !vegToggle._clickBound) {
    vegToggle._clickBound = true;
    vegToggle.addEventListener('click', () => {
      setVegFilter(!vegFilterActive);
    });
  }
});

function setVegFilter(on) {
  vegFilterActive = on;
  const vegLegend = document.getElementById('veg-legend');
  if (vegLegend) vegLegend.classList.toggle('active-filter', on);
  const stateEl = document.getElementById('veg-filter-state');
  if (stateEl) stateEl.style.display = on ? 'inline' : 'none';
  const vegToggle = document.getElementById('veg-toggle');
  if (vegToggle) {
    vegToggle.classList.toggle('active', on);
    vegToggle.classList.toggle('btn-outline-success', !on);
    vegToggle.classList.toggle('btn-success', on);
  }
  performSearch();
}

async function performSearch() {
  if (currentAbort) currentAbort.abort();
  currentAbort = new AbortController();
  const signal = currentAbort.signal;

  const district = document.getElementById('filter-district')?.value;
  const sort = document.getElementById('sort-by')?.value;
  let lat = document.getElementById('user-lat')?.value;
  let lng = document.getElementById('user-lng')?.value;

  const params = new URLSearchParams();
  if (district) params.set('district', district);
  if (sort && sort !== 'relevance') params.set('sort', sort);
  if (lat) params.set('lat', lat);
  if (lng) params.set('lng', lng);
  if (vegFilterActive) params.set('veg', '1');

  const clearBtn = document.getElementById('clear-filters');
  const hasFilters = district || (sort && sort !== 'relevance') || lat;
  if (clearBtn) clearBtn.style.display = hasFilters ? 'inline-flex' : 'none';

  try {
    const resultsEl = document.getElementById('results');
    if (resultsEl) resultsEl.innerHTML = '<div class="text-center py-5"><div class="loading-spinner mx-auto mb-2" style="width:2rem;height:2rem;"></div><p class="text-muted">Searching...</p></div>';
    const resp = await fetch(`/api/centres?${params}`, { signal });
    let centres = await resp.json();
    if (michelinFilterActive) {
      centres = centres.filter(c => c.michelin_count > 0);
    }
    const michelinCountEl = document.getElementById('michelin-count');
    if (michelinCountEl) {
      const total = centres.reduce((sum, c) => sum + (c.michelin_count || 0), 0);
      michelinCountEl.textContent = total;
      michelinCountEl.style.display = total > 0 ? 'inline' : 'none';
    }
    const vegCountEl = document.getElementById('veg-count');
    if (vegCountEl) {
      const vTotal = centres.reduce((sum, c) => sum + (c.veg_count || 0), 0);
      vegCountEl.textContent = vTotal;
      vegCountEl.style.display = vTotal > 0 ? 'inline' : 'none';
    }
    const countEl = document.getElementById('result-count');
    if (countEl) countEl.innerHTML = `<strong>${centres.length}</strong> hawker centre${centres.length !== 1 ? 's' : ''} found`;
    initMap(centres, lat ? parseFloat(lat) : null, lng ? parseFloat(lng) : null);
    const container = document.getElementById('results');
    if (!container) return;
    if (centres.length === 0) {
      if (lat && lng) {
        const nParams = new URLSearchParams();
        nParams.set('lat', lat);
        nParams.set('lng', lng);
        nParams.set('sort', 'distance');
        const nResp = await fetch(`/api/centres?${nParams}`, { signal });
        centres = await nResp.json();
        if (centres.length === 0) {
          container.innerHTML = '<div class="text-center py-5"><i class="bi bi-map display-4" style="color:#ccc;"></i><p class="mt-3 text-muted">No hawker centres found in this area.</p></div>';
          return;
        }
        container.innerHTML = '<div class="alert alert-info py-2 px-3 mb-3 small">No matches — showing nearest centres instead</div>';
      } else {
        container.innerHTML = '<div class="text-center py-5"><i class="bi bi-search display-4" style="color:#ccc;"></i><p class="mt-3 text-muted">No hawker centres found matching your search.</p></div>';
        return;
      }
    }
    const shown = centres.slice(0, PAGE_SIZE);
    _lastResults = centres;
    container.innerHTML = shown.map((c, i) => renderCard(c, i)).join('');
    const legendEl = document.getElementById('michelin-legend');
    if (legendEl) {
      if (!legendEl._clickBound) {
        legendEl._clickBound = true;
        legendEl.addEventListener('click', () => {
          michelinFilterActive = !michelinFilterActive;
          legendEl.classList.toggle('active-filter', michelinFilterActive);
          performSearch();
        });
      }
    }
    if (centres.length > PAGE_SIZE) {
      const remaining = centres.length - PAGE_SIZE;
      container.insertAdjacentHTML('beforeend',
        '<div class="text-center py-3 results-footer">' +
        '<button class="btn btn-outline-primary btn-sm rounded-pill px-4 load-more-btn py-2" data-loaded="1" data-total="' + centres.length + '" data-params="' + params.toString() + '" onclick="loadMore(this)">Show ' + Math.min(PAGE_SIZE, remaining) + ' more <i class="bi bi-arrow-down ms-1"></i></button>' +
        '<br><small class="text-muted mt-2 d-block">' + PAGE_SIZE + ' of ' + centres.length + ' centres shown</small></div>');
    } else {
      container.insertAdjacentHTML('beforeend', '<div class="text-center py-3"><small class="text-muted">✓ All <strong>' + centres.length + '</strong> centres shown</small></div>');
    }
  } catch(e) {
    if (e.name === 'AbortError') return;
    console.error('Search error:', e);
    const resultsEl = document.getElementById('results');
    if (resultsEl) resultsEl.innerHTML = '<div class="text-center py-5"><i class="bi bi-exclamation-triangle display-4 text-warning"></i><p class="mt-3 text-muted">Search failed. Please try again.</p></div>';
  }
  updateRainfallBadge(lat, lng);
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function setLocationCookie(lat, lng) {
  const d = new Date();
  d.setTime(d.getTime() + 30 * 24 * 60 * 60 * 1000);
  document.cookie = `hawker_lat=${lat}; expires=${d.toUTCString()}; path=/`;
  document.cookie = `hawker_lng=${lng}; expires=${d.toUTCString()}; path=/`;
}

function getLocationCookie() {
  const match = document.cookie.match(/hawker_lat=([^;]+)/);
  const match2 = document.cookie.match(/hawker_lng=([^;]+)/);
  if (match && match2) {
    return {lat: parseFloat(match[1]), lng: parseFloat(match2[1])};
  }
  return null;
}

let _rainfallTimer = null;
let _lastResults = [];

function loadMore(btn) {
  if (!btn) return;
  const loaded = parseInt(btn.dataset.loaded);
  const start = loaded * PAGE_SIZE;
  if (start >= _lastResults.length) return;
  const nextPage = _lastResults.slice(start, start + PAGE_SIZE);
  const rendered = nextPage.map((c, i) => renderCard(c, start + i)).join('');
  const footer = btn.closest('.results-footer');
  if (footer) footer.remove();
  const container = document.getElementById('results');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', rendered);
  const remaining = _lastResults.length - (start + nextPage.length);
  if (remaining > 0) {
    container.insertAdjacentHTML('beforeend',
      '<div class="text-center py-3 results-footer">' +
      '<button class="btn btn-outline-primary btn-sm rounded-pill px-4 load-more-btn py-2" data-loaded="' + (loaded + 1) + '" onclick="loadMore(this)">Show ' + Math.min(PAGE_SIZE, remaining) + ' more <i class="bi bi-arrow-down ms-1"></i></button>' +
      '<br><small class="text-muted mt-2 d-block">' + (start + nextPage.length) + ' of ' + _lastResults.length + ' centres shown</small></div>');
  } else {
    container.insertAdjacentHTML('beforeend', '<div class="text-center py-3"><small class="text-muted">✓ All <strong>' + _lastResults.length + '</strong> centres shown</small></div>');
  }
}

async function updateRainfallBadge(lat, lng) {
  const overlay = document.getElementById('weather-overlay');
  const iconEl = document.getElementById('weather-icon');
  const textEl = document.getElementById('weather-text');
  if (!overlay || !iconEl || !textEl) return;
  if (!lat || !lng) { overlay.style.display = 'none'; return; }
  try {
    const resp = await fetch(`/api/rainfall/nearest?lat=${lat}&lng=${lng}`);
    if (!resp.ok) { overlay.style.display = 'none'; return; }
    const data = await resp.json();
    if (!data || data.rainfall_mm === undefined) { overlay.style.display = 'none'; return; }
    const mm = data.rainfall_mm;
    const dist = data.distance_km;
    const ts = data.timestamp || '';
    let icon, borderColor;
    if (mm === 0) { icon = '☀️'; borderColor = '#90caf9'; }
    else if (mm < 2) { icon = '🌦'; borderColor = '#ffcc80'; }
    else if (mm < 10) { icon = '🌧'; borderColor = '#64b5f6'; }
    else { icon = '⛈'; borderColor = '#b39ddb'; }
    iconEl.textContent = icon;
    const timeStr = ts ? ts.slice(11, 16) : '';
    textEl.textContent = `${mm}mm · ${dist}km · ${timeStr}`;
    overlay.title = `Station: ${data.station_name} · ${dist}km away · ${ts}`;
    overlay.style.cssText = `display:flex; position:absolute; bottom:0; left:0; margin:12px; padding:4px 10px; border-radius:8px; z-index:1000; background:rgba(255,255,255,0.92); backdrop-filter:blur(4px); font-size:13px; font-weight:500; gap:6px; align-items:center; border:1px solid ${borderColor}; box-shadow:0 2px 8px rgba(0,0,0,0.1);`;
    clearTimeout(_rainfallTimer);
    _rainfallTimer = setTimeout(() => updateRainfallBadge(lat, lng), 120000);
  } catch(_) { overlay.style.display = 'none'; }
}

// ── Stall Search ──
const stallSearch = document.getElementById('stall-search');
const stallResults = document.getElementById('stall-results');
const clearStallSearch = document.getElementById('clear-stall-search');

if (stallSearch && stallResults) {
  let stallSearchTimer = null;

  stallSearch.addEventListener('input', () => {
    const q = stallSearch.value.trim();
    if (clearStallSearch) {
      clearStallSearch.classList.toggle('d-none', !q);
    }
    if (q.length < 2) {
      stallResults.classList.add('d-none');
      return;
    }
    clearTimeout(stallSearchTimer);
    stallSearchTimer = setTimeout(async () => {
      try {
        const resp = await fetch(`/api/search-stalls?q=${encodeURIComponent(q)}`);
        const data = await resp.json();
        if (data.length === 0) {
          stallResults.innerHTML = '<div class="stall-no-results">No stalls found for "<strong>' + q + '</strong>"</div>';
        } else {
          stallResults.innerHTML = data.map((s, i) => {
            const foodTags = s.food && s.food.length > 0
              ? '<div class="stall-food mt-1">' + s.food.map(f => '<span>' + f + '</span>').join(' ') + '</div>'
              : '';
            const michelinBadge = s.michelin
              ? ' <img src="/static/img/bib-gourmand-icon.png" style="width:1em;height:0.9em;vertical-align:middle;" alt="BG">'
              : '';
            return '<div class="stall-result-item" onclick="window.location.href=\'/centre/' + s.centre_id + '\'">' +
              '<div class="stall-icon">🍜</div>' +
              '<div>' +
                '<div class="stall-name">' + escHtml(s.stall_name) + michelinBadge + '</div>' +
                '<div class="stall-centre"><i class="bi bi-geo-alt" style="font-size:0.65rem;"></i> ' + escHtml(s.centre_name) +
                  (s.district ? ' · ' + escHtml(s.district) : '') + '</div>' +
                foodTags +
                (s.description ? '<div class="stall-centre mt-1" style="font-size:0.75rem;color:#666;">' + escHtml(s.description).slice(0, 80) + '</div>' : '') +
              '</div></div>';
          }).join('');
        }
        stallResults.classList.remove('d-none');
      } catch(e) {
        if (e.name !== 'AbortError') console.error('Stall search error:', e);
      }
    }, 250);
  });

  // Close dropdown on click outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.stall-search-group') && !e.target.closest('#stall-results')) {
      stallResults.classList.add('d-none');
    }
  });

  // Clear button
  if (clearStallSearch) {
    clearStallSearch.addEventListener('click', () => {
      stallSearch.value = '';
      stallResults.classList.add('d-none');
      clearStallSearch.classList.add('d-none');
      stallSearch.focus();
    });
  }
}

function escHtml(s) {
  if (s === null || s === undefined) return '';
  if (typeof s !== 'string') s = String(s);
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── AI Craving Assistant ─────────────────────────────
(function(){
  const input = document.getElementById('craving-input');
  const btn = document.getElementById('craving-btn');
  const surprise = document.getElementById('craving-surprise');
  const results = document.getElementById('craving-results');
  if (!input || !btn || !surprise || !results) return;

  function esc(x){ if(!x) return ''; return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  async function run(payload){
    results.classList.remove('d-none');
    results.innerHTML = '<div class="loading-spinner mb-2" style="width:1.5rem;height:1.5rem;"></div><small class="text-muted">AI is cooking up recommendations…</small>';
    try {
      const resp = await fetch('/api/craving', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      const data = await resp.json();
      if (data.error){ results.innerHTML = '<div class="alert alert-warning mb-0">'+escHtml(data.error)+'</div>'; return; }
      if (!data.recommendations || data.recommendations.length===0){
        results.innerHTML = '<div class="alert alert-info mb-0">No matches. Try a more common dish like "chicken rice" or "laksa".</div>'; return;
      }
      const cards = data.recommendations.map(r => {
              const m = r.michelin ? '<span class="badge" style="background:#daa520;color:#fff;">⭐ Michelin</span>' : '';
              const veg = r.veg ? '<span class="veg-badge" style="display:inline-flex;align-items:center;gap:2px;font-size:.68rem;font-weight:700;color:#1b5e20;background:#e8f5e9;border:1px solid #4caf50;border-radius:10px;padding:1px 7px;margin-left:4px;">🌱 Veg</span>' : '';
              const halal = r.halal ? '<span class="halal-badge" style="font-size:.68rem;font-weight:700;color:#155724;background:#d4edda;border:1px solid #28a745;border-radius:10px;padding:1px 7px;margin-left:4px;">🟢 Halal</span>' : '';
              const dishes = r.dishes && r.dishes.length ? '<div class="small text-muted mt-1">'+r.dishes.slice(0,4).map(escHtml).join(' · ')+'</div>' : '';
              return `<div class="card mb-2 border-0 shadow-sm">
                <div class="card-body py-2">
                  <div class="d-flex justify-content-between align-items-center">
                    <strong>🍽️ ${escHtml(r.stall)}</strong> <span>${m}${veg}${halal}</span>
                  </div>
                  <div class="small text-muted">${r.centre_id ? '📍 <a href="/centre/' + escHtml(r.centre_id) + '" class="text-decoration-none">' + escHtml(r.centre) + '</a>' : '🍽️ ' + escHtml(r.centre)}</div>
                  ${dishes}
                  <div class="small mt-1" style="color:#e6532f;">💡 ${escHtml(r.reason)}</div>
                </div>
              </div>`;
            }).join('');
      results.innerHTML = cards;
    } catch(e){
      results.innerHTML = '<div class="alert alert-danger mb-0">Something went wrong. Please try again.</div>';
    }
  }

  btn.addEventListener('click', () => {
    const q = input.value.trim();
    if (!q) { input.focus(); return; }
    run({query:q});
  });
  input.addEventListener('keydown', (e) => { if(e.key==='Enter'){ btn.click(); } });
  surprise.addEventListener('click', () => run({surprise:true}));
})();

// ── Non-Hawker Food Spots markers ───────────────────
async function loadSpots() {
  if (!spotsLayer || !map) return;
  try {
    const resp = await fetch('/api/spots');
    const spots = await resp.json();
    spots.forEach((s) => {
      if (!s.lat || !s.lng) return;
      const icon = L.divIcon({
        html: `<div style="background:#455A64;color:#fff;border-radius:8px;width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 2px 8px rgba(0,0,0,.35);border:2px solid #FFD54F;transform:scale(1);">🍽️</div>`,
        iconSize: [30, 30], iconAnchor: [15, 15], className: 'non-hawker-spot'
      });
      const cuisine = s.cuisine ? `<br><small style="color:#888;">${escHtml(s.cuisine)}</small>` : '';
      const price = s.price ? `<span class="badge rounded-pill" style="background:#E8F5E9;color:#2E7D32;font-size:.6rem;">${escHtml(s.price)}</span>` : '';
      L.marker([s.lat, s.lng], {icon})
        .bindPopup(`<div style="min-width:200px;font-family:'Inter',sans-serif;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:1.4rem;">🍽️</span>
            <b style="font-size:1rem;color:#1a1a1a;">${escHtml(s.name)}</b>
          </div>
          ${cuisine}
          ${s.address ? `<small style="color:#888;">📍 ${escHtml(s.address)}</small><br>` : ''}
          <div style="margin-top:6px;">${price} <span class="badge rounded-pill" style="background:#FFF3E0;color:#E65100;font-size:.6rem;"><i class="bi bi-shop"></i> ${escHtml(s.category || 'Michelin')}</span></div>
          <div style="margin-top:6px;text-align:center;font-size:.65rem;color:#bbb;border-top:1px solid #eee;padding-top:6px;">✕ tap outside to close</div>
        </div>`)
        .addTo(spotsLayer);
    });
  } catch(e) {
    console.error('Spots load error:', e);
  }
}
