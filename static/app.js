const PantryChef = (() => {
  const CONSENT_STORAGE_KEY = 'pantrychef_consent_v1';
  const AUTH_MODE_STORAGE_KEY = 'pantrychef_auth_mode';
  const SAVED_KEYS_STORAGE_KEY = 'pantrychef_saved_keys_v1';

  let shellInitialized = false;
  let currentUser = null;
  let activeAuthTab = 'login';

  function loadSavedKeys() {
    try {
      const raw = sessionStorage.getItem(SAVED_KEYS_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch {
      return new Set();
    }
  }

  function persistSavedKeys(setObj) {
    try {
      sessionStorage.setItem(SAVED_KEYS_STORAGE_KEY, JSON.stringify(Array.from(setObj)));
    } catch {
      /* ignore storage quota errors */
    }
  }

  function getRecipeKey(recipe) {
    if (!recipe || typeof recipe !== 'object') return '';
    const name = String(recipe.name || '').trim().toLowerCase();
    const cookTime = String(recipe.cook_time || '').trim().toLowerCase();
    return `${name}::${cookTime}`;
  }

  function escapeHTML(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatCalories(val) {
    const raw = String(val ?? '').trim();
    if (!raw) return null;
    const escaped = escapeHTML(raw);
    return /\b(k?cal|calories?)\b/i.test(raw) ? escaped : `${escaped} kcal`;
  }

  function formatProtein(val) {
    const raw = String(val ?? '').trim();
    if (!raw) return null;
    const escaped = escapeHTML(raw);
    return /\bprotein\b/i.test(raw) ? escaped : `${escaped} protein`;
  }

  function recipeCardHTML(recipe, index, savedKeys) {
    const ingredients = (recipe.ingredients || [])
      .map(i => `<li>${escapeHTML(i.amount || '')} ${escapeHTML(i.item || '')}</li>`)
      .join('');
    const steps = (recipe.steps || [])
      .map(s => `<li>${escapeHTML(s)}</li>`)
      .join('');
    const nutrition = recipe.nutrition || {};
    const nutritionLine = [
      formatCalories(nutrition.calories),
      formatProtein(nutrition.protein),
    ].filter(Boolean).join(' &middot; ');

    const isAlreadySaved = savedKeys && savedKeys.has(getRecipeKey(recipe));
    const btnClass = isAlreadySaved ? 'btn save-btn saved' : 'btn save-btn';
    const btnText = isAlreadySaved ? '★ Saved' : '☆ Save to Favorites';
    const btnDisabled = isAlreadySaved ? 'disabled' : '';

    return `
      <div class="recipe-card" data-index="${index}">
        <h3>${escapeHTML(recipe.name || 'Untitled recipe')}</h3>
        <div class="recipe-meta">${escapeHTML(recipe.cook_time || '')}</div>
        ${ingredients ? `<h4>Ingredients</h4><ul>${ingredients}</ul>` : ''}
        ${steps ? `<h4>Steps</h4><ol>${steps}</ol>` : ''}
        ${nutritionLine ? `<div class="nutrition-line">${nutritionLine}</div>` : ''}
        <div class="card-footer">
          <button type="button" class="${btnClass}" data-index="${index}" ${btnDisabled}>${btnText}</button>
        </div>
      </div>
    `;
  }

  function hasValidConsent() {
    try {
      return (
        localStorage.getItem(CONSENT_STORAGE_KEY) === 'true' &&
        Boolean(localStorage.getItem(AUTH_MODE_STORAGE_KEY))
      );
    } catch {
      return false;
    }
  }

  function saveConsentState(mode) {
    try {
      localStorage.setItem(CONSENT_STORAGE_KEY, 'true');
      localStorage.setItem(AUTH_MODE_STORAGE_KEY, mode);
    } catch {
      /* ignore private browsing restrictions */
    }
  }

  function clearConsentAuthMode() {
    try {
      localStorage.removeItem(AUTH_MODE_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }

  function playChefLoadingTransition(onComplete) {
    const screen = document.getElementById('chefLoadingScreen');
    if (!screen) {
      if (onComplete) onComplete();
      return;
    }

    const prefersReducedMotion =
      window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const durationMs = prefersReducedMotion ? 120 : 1050;

    screen.hidden = false;
    screen.classList.remove('is-fading-out');
    screen.classList.add('is-active');

    window.setTimeout(() => {
      screen.classList.add('is-fading-out');
      window.setTimeout(() => {
        screen.classList.remove('is-active', 'is-fading-out');
        screen.hidden = true;
        if (onComplete) onComplete();
      }, prefersReducedMotion ? 20 : 180);
    }, durationMs);
  }

  function updateNavAccountUI() {
    const badge = document.getElementById('navUserBadge');
    const exportBtn = document.getElementById('exportDataBtn');
    const deleteBtn = document.getElementById('deleteAccountBtn');
    const signOutBtn = document.getElementById('signOutBtn');

    if (!badge) return;

    if (currentUser && currentUser.username) {
      badge.textContent = `Chef ${currentUser.display_name || currentUser.username}`;
      badge.classList.add('is-authenticated');
      if (exportBtn) exportBtn.hidden = false;
      if (deleteBtn) deleteBtn.hidden = false;
      if (signOutBtn) signOutBtn.textContent = 'Sign Out';
    } else {
      badge.textContent = 'Guest Chef';
      badge.classList.remove('is-authenticated');
      if (exportBtn) exportBtn.hidden = true;
      if (deleteBtn) deleteBtn.hidden = true;
      if (signOutBtn) signOutBtn.textContent = 'Sign Out';
    }
  }

  function setAuthTab(tabName) {
    activeAuthTab = tabName;
    const tabs = document.querySelectorAll('[data-auth-tab]');
    tabs.forEach(tab => {
      const isMatch = tab.dataset.authTab === tabName;
      tab.classList.toggle('active', isMatch);
      tab.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    const displayNameField = document.getElementById('authDisplayNameField');
    const credentialsFields = document.getElementById('authCredentialsFields');
    const guestNote = document.getElementById('authGuestNote');
    const submitBtn = document.getElementById('authSubmitBtn');
    const quickGuestBtn = document.getElementById('quickGuestBtn');
    const authError = document.getElementById('authGateError');
    const passwordInput = document.getElementById('authPassword');

    if (authError) authError.style.display = 'none';

    if (tabName === 'register') {
      if (displayNameField) displayNameField.hidden = false;
      if (credentialsFields) credentialsFields.hidden = false;
      if (guestNote) guestNote.hidden = true;
      if (submitBtn) submitBtn.textContent = 'Create Account & Enter Kitchen';
      if (quickGuestBtn) quickGuestBtn.hidden = false;
      if (passwordInput) passwordInput.setAttribute('autocomplete', 'new-password');
    } else if (tabName === 'guest') {
      if (displayNameField) displayNameField.hidden = true;
      if (credentialsFields) credentialsFields.hidden = true;
      if (guestNote) guestNote.hidden = false;
      if (submitBtn) submitBtn.textContent = 'Enter as Quick Guest Chef';
      if (quickGuestBtn) quickGuestBtn.hidden = true;
    } else {
      if (displayNameField) displayNameField.hidden = true;
      if (credentialsFields) credentialsFields.hidden = false;
      if (guestNote) guestNote.hidden = true;
      if (submitBtn) submitBtn.textContent = 'Sign In & Enter Kitchen';
      if (quickGuestBtn) quickGuestBtn.hidden = false;
      if (passwordInput) passwordInput.setAttribute('autocomplete', 'current-password');
    }
  }

  function openAuthGate(defaultTab) {
    const gate = document.getElementById('chefAuthGate');
    if (!gate) return;
    if (defaultTab) setAuthTab(defaultTab);

    const consentData = document.getElementById('consentDataCheck');
    const consentDisclaimer = document.getElementById('consentDisclaimerCheck');
    try {
      if (localStorage.getItem(CONSENT_STORAGE_KEY) === 'true') {
        if (consentData) consentData.checked = true;
        if (consentDisclaimer) consentDisclaimer.checked = true;
      }
    } catch {
      /* ignore */
    }

    gate.hidden = false;
  }

  function closeAuthGateWithTransition(shouldReloadFavorites) {
    const gate = document.getElementById('chefAuthGate');
    if (gate) gate.hidden = true;
    updateNavAccountUI();
    playChefLoadingTransition(() => {
      if (shouldReloadFavorites && window.location.pathname === '/favorites') {
        window.location.reload();
      }
    });
  }

  function validateConsentCheckboxes() {
    const consentData = document.getElementById('consentDataCheck');
    const consentDisclaimer = document.getElementById('consentDisclaimerCheck');
    const authError = document.getElementById('authGateError');

    if (!consentData || !consentDisclaimer) return true;
    if (!consentData.checked || !consentDisclaimer.checked) {
      if (authError) {
        authError.textContent =
          'Please check both the data processing consent and the culinary/allergen safety disclaimer to continue.';
        authError.style.display = 'block';
      }
      return false;
    }
    return true;
  }

  function initShell() {
    if (shellInitialized) return;
    shellInitialized = true;

    const gate = document.getElementById('chefAuthGate');
    const authForm = document.getElementById('chefAuthForm');
    const quickGuestBtn = document.getElementById('quickGuestBtn');
    const authError = document.getElementById('authGateError');
    const signOutBtn = document.getElementById('signOutBtn');
    const exportDataBtn = document.getElementById('exportDataBtn');
    const deleteAccountBtn = document.getElementById('deleteAccountBtn');
    const footerAuthTrigger = document.getElementById('footerAuthTrigger');
    const legalModal = document.getElementById('legalModal');
    const closeLegalModalBtn = document.getElementById('closeLegalModalBtn');
    const ackLegalModalBtn = document.getElementById('ackLegalModalBtn');

    document.querySelectorAll('[data-auth-tab]').forEach(tabBtn => {
      tabBtn.addEventListener('click', () => setAuthTab(tabBtn.dataset.authTab));
    });

    if (quickGuestBtn) {
      quickGuestBtn.addEventListener('click', () => {
        if (!validateConsentCheckboxes()) return;
        currentUser = null;
        saveConsentState('guest');
        closeAuthGateWithTransition(false);
      });
    }

    if (authForm) {
      authForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (authError) authError.style.display = 'none';
        if (!validateConsentCheckboxes()) return;

        if (activeAuthTab === 'guest') {
          currentUser = null;
          saveConsentState('guest');
          closeAuthGateWithTransition(false);
          return;
        }

        const usernameInput = document.getElementById('authUsername');
        const passwordInput = document.getElementById('authPassword');
        const displayNameInput = document.getElementById('authDisplayName');
        const submitBtn = document.getElementById('authSubmitBtn');

        const username = usernameInput ? usernameInput.value.trim() : '';
        const password = passwordInput ? passwordInput.value : '';
        const displayName = displayNameInput ? displayNameInput.value.trim() : '';

        if (!username || !password) {
          if (authError) {
            authError.textContent = 'Please enter both a username and password (or choose Quick Guest Chef).';
            authError.style.display = 'block';
          }
          return;
        }

        const endpoint = activeAuthTab === 'register' ? '/api/auth/register' : '/api/auth/login';
        const payload =
          activeAuthTab === 'register'
            ? { username, password, display_name: displayName || username, consent_accepted: true }
            : { username, password };

        if (submitBtn) submitBtn.disabled = true;
        try {
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          if (!res.ok) {
            if (authError) {
              authError.textContent = data.error || 'Authentication failed. Please check your credentials.';
              authError.style.display = 'block';
            }
          } else {
            currentUser = data.user || null;
            saveConsentState('user');
            if (passwordInput) passwordInput.value = '';
            closeAuthGateWithTransition(true);
          }
        } catch (err) {
          if (authError) {
            authError.textContent = 'Could not reach server: ' + err.message;
            authError.style.display = 'block';
          }
        } finally {
          if (submitBtn) submitBtn.disabled = false;
        }
      });
    }

    if (signOutBtn) {
      signOutBtn.addEventListener('click', async () => {
        try {
          await fetch('/api/auth/logout', { method: 'POST' });
        } catch {
          /* ignore network failure on logout */
        }
        currentUser = null;
        clearConsentAuthMode();
        updateNavAccountUI();
        openAuthGate('login');
      });
    }

    if (footerAuthTrigger) {
      footerAuthTrigger.addEventListener('click', () => {
        openAuthGate(currentUser ? 'login' : activeAuthTab);
      });
    }

    if (exportDataBtn) {
      exportDataBtn.addEventListener('click', async () => {
        exportDataBtn.disabled = true;
        try {
          const res = await fetch('/api/account/export');
          if (res.ok) {
            const payload = await res.json();
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `pantrychef-export-${(currentUser && currentUser.username) || 'chef'}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
          }
        } catch {
          /* ignore */
        } finally {
          exportDataBtn.disabled = false;
        }
      });
    }

    if (deleteAccountBtn) {
      deleteAccountBtn.addEventListener('click', async () => {
        const confirmed = window.confirm(
          'Permanently delete your PantryChef account and all saved recipes? This action cannot be undone.'
        );
        if (!confirmed) return;

        deleteAccountBtn.disabled = true;
        try {
          const res = await fetch('/api/account', { method: 'DELETE' });
          if (res.ok) {
            currentUser = null;
            clearConsentAuthMode();
            updateNavAccountUI();
            openAuthGate('register');
            if (window.location.pathname === '/favorites') {
              window.location.reload();
            }
          }
        } catch {
          /* ignore */
        } finally {
          deleteAccountBtn.disabled = false;
        }
      });
    }

    function openLegalModal(section) {
      if (!legalModal) return;
      legalModal.hidden = false;
      const targetId = section === 'terms' ? 'legalTermsSection' : 'legalPrivacySection';
      const targetEl = document.getElementById(targetId);
      if (targetEl && typeof targetEl.scrollIntoView === 'function') {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    function closeLegalModal() {
      if (legalModal) legalModal.hidden = true;
    }

    document.querySelectorAll('[data-open-legal]').forEach(btn => {
      btn.addEventListener('click', () => openLegalModal(btn.dataset.openLegal));
    });

    if (closeLegalModalBtn) closeLegalModalBtn.addEventListener('click', closeLegalModal);
    if (ackLegalModalBtn) ackLegalModalBtn.addEventListener('click', closeLegalModal);
    if (legalModal) {
      legalModal.addEventListener('click', (e) => {
        if (e.target === legalModal) closeLegalModal();
      });
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && legalModal && !legalModal.hidden) {
        closeLegalModal();
      }
    });

    // Sync initial auth state with backend session
    (async () => {
      try {
        const res = await fetch('/api/auth/me');
        if (res.ok) {
          const data = await res.json();
          currentUser = data.user || null;
          if (currentUser) {
            saveConsentState('user');
          }
        }
      } catch {
        /* ignore offline/test environment */
      }
      updateNavAccountUI();
      if (gate && !hasValidConsent()) {
        openAuthGate('login');
      }
    })();
  }

  function initGenerator() {
    initShell();

    let lastRecipes = [];
    let lastModelUsed = null;
    const savedKeys = loadSavedKeys();

    const servingsInput = document.getElementById('servings');
    const servingsVal = document.getElementById('servingsVal');
    if (servingsInput && servingsVal) {
      servingsInput.addEventListener('input', () => {
        servingsVal.textContent = servingsInput.value;
      });
    }

    const form = document.getElementById('generatorForm');
    const ingredientsInput = document.getElementById('ingredients');
    const btn = document.getElementById('generateBtn');
    const status = document.getElementById('status');
    const errorBox = document.getElementById('errorBox');
    const results = document.getElementById('results');
    const recipeGrid = document.getElementById('recipeGrid');

    if (!btn) return;

    async function runGenerate() {
      if (btn.disabled) return;

      if (!hasValidConsent() && document.getElementById('chefAuthGate')) {
        openAuthGate('login');
        return;
      }

      const ingredients = ingredientsInput ? ingredientsInput.value.trim() : '';
      const dietEl = document.getElementById('diet');
      const cuisineEl = document.getElementById('cuisine');
      const diet = dietEl ? dietEl.value : 'No restriction';
      const cuisine = cuisineEl ? cuisineEl.value : 'Any';
      const servings = servingsInput ? servingsInput.value : 2;
      const modelField = document.getElementById('modelField');
      const model = modelField && modelField.value && modelField.value.trim()
        ? modelField.value.trim()
        : null;

      errorBox.style.display = 'none';
      results.style.display = 'none';

      if (!ingredients) {
        errorBox.textContent = 'Please enter at least one ingredient.';
        errorBox.style.display = 'block';
        return;
      }

      btn.disabled = true;
      status.style.display = 'block';

      try {
        const res = await fetch('/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ingredients, diet, cuisine, servings, model })
        });
        const data = await res.json();

        if (!res.ok) {
          errorBox.textContent = data.error || 'Something went wrong.';
          errorBox.style.display = 'block';
        } else {
          lastRecipes = data.recipes || [];
          lastModelUsed = data.model_used || null;
          recipeGrid.innerHTML = lastRecipes
            .map((r, idx) => recipeCardHTML(r, idx, savedKeys))
            .join('');
          results.style.display = 'block';
        }
      } catch (err) {
        errorBox.textContent = 'Could not reach the server: ' + err.message;
        errorBox.style.display = 'block';
      } finally {
        btn.disabled = false;
        status.style.display = 'none';
      }
    }

    if (form) {
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        runGenerate();
      });
    } else {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        runGenerate();
      });
    }

    if (ingredientsInput) {
      ingredientsInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          runGenerate();
        }
      });
    }

    if (recipeGrid) {
      recipeGrid.addEventListener('click', async (e) => {
        const saveBtn = e.target.closest('.save-btn');
        if (!saveBtn || saveBtn.disabled || saveBtn.classList.contains('saved')) return;

        const idx = Number(saveBtn.dataset.index);
        const recipe = lastRecipes[idx];
        if (!recipe) return;

        const key = getRecipeKey(recipe);
        if (key && savedKeys.has(key)) {
          saveBtn.textContent = '★ Saved';
          saveBtn.classList.add('saved');
          saveBtn.disabled = true;
          return;
        }

        saveBtn.disabled = true;
        try {
          const res = await fetch('/favorites', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ recipe, model_used: lastModelUsed })
          });
          if (res.ok) {
            if (key) {
              savedKeys.add(key);
              persistSavedKeys(savedKeys);
            }
            saveBtn.textContent = '★ Saved';
            saveBtn.classList.add('saved');
            saveBtn.disabled = true;
          } else {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Could not save — try again';
          }
        } catch {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Could not save — try again';
        }
      });
    }
  }

  function initFavorites() {
    initShell();

    const filterInput = document.getElementById('favoritesFilter');
    const countBadge = document.getElementById('favoritesCount');
    const noMatchBox = document.getElementById('favoritesNoMatch');

    function updateFavoritesFilter() {
      const cards = Array.from(document.querySelectorAll('.favorites-grid .recipe-card'));
      const query = filterInput ? filterInput.value.trim().toLowerCase() : '';
      let visibleCount = 0;

      cards.forEach(card => {
        const text = (card.textContent || '').toLowerCase();
        const matches = !query || text.includes(query);
        card.style.display = matches ? '' : 'none';
        if (matches) visibleCount += 1;
      });

      if (countBadge) {
        countBadge.textContent = query
          ? `${visibleCount} of ${cards.length} shown`
          : `${cards.length} saved`;
      }
      if (noMatchBox) {
        noMatchBox.hidden = !(cards.length > 0 && visibleCount === 0);
      }
    }

    if (filterInput) {
      filterInput.addEventListener('input', updateFavoritesFilter);
    }

    document.querySelectorAll('.stars.interactive').forEach(starGroup => {
      starGroup.addEventListener('click', async (e) => {
        const star = e.target.closest('.star');
        if (!star) return;
        const id = starGroup.dataset.id;
        const value = Number(star.dataset.value);

        try {
          const res = await fetch(`/favorites/${id}/rating`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rating: value })
          });
          if (res.ok) {
            starGroup.dataset.rating = String(value);
            starGroup.querySelectorAll('.star').forEach(s => {
              const isFilled = Number(s.dataset.value) <= value;
              s.classList.toggle('filled', isFilled);
              s.setAttribute('aria-pressed', isFilled ? 'true' : 'false');
            });
          }
        } catch { /* silently ignore — rating is non-critical */ }
      });
    });

    document.querySelectorAll('.delete-btn').forEach(delBtn => {
      delBtn.addEventListener('click', async () => {
        const id = delBtn.dataset.id;
        delBtn.disabled = true;
        try {
          const res = await fetch(`/favorites/${id}`, { method: 'DELETE' });
          if (res.ok) {
            const card = delBtn.closest('.recipe-card');
            if (card) card.remove();
            updateFavoritesFilter();
          } else {
            delBtn.disabled = false;
          }
        } catch {
          delBtn.disabled = false;
        }
      });
    });
  }

  return { initShell, initGenerator, initFavorites, escapeHTML };
})();

