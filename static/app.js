const PantryChef = (() => {

  function recipeCardHTML(recipe, index) {
    const ingredients = (recipe.ingredients || [])
      .map(i => `<li>${escapeHTML(i.amount || '')} ${escapeHTML(i.item || '')}</li>`)
      .join('');
    const steps = (recipe.steps || [])
      .map(s => `<li>${escapeHTML(s)}</li>`)
      .join('');
    const nutrition = recipe.nutrition || {};
    const nutritionLine = [
      nutrition.calories ? `${escapeHTML(nutrition.calories)} kcal` : null,
      nutrition.protein ? `${escapeHTML(nutrition.protein)} protein` : null,
    ].filter(Boolean).join(' &middot; ');

    return `
      <div class="recipe-card" data-index="${index}">
        <h3>${escapeHTML(recipe.name || 'Untitled recipe')}</h3>
        <div class="recipe-meta">${escapeHTML(recipe.cook_time || '')}</div>
        ${ingredients ? `<h4>Ingredients</h4><ul>${ingredients}</ul>` : ''}
        ${steps ? `<h4>Steps</h4><ol>${steps}</ol>` : ''}
        ${nutritionLine ? `<div class="nutrition-line">${nutritionLine}</div>` : ''}
        <div class="card-footer">
          <button class="btn save-btn" data-index="${index}">☆ Save to Favorites</button>
        </div>
      </div>
    `;
  }

  function escapeHTML(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function initGenerator() {
    let lastRecipes = [];
    let lastModelUsed = null;

    const servingsInput = document.getElementById('servings');
    const servingsVal = document.getElementById('servingsVal');
    servingsInput.addEventListener('input', () => servingsVal.textContent = servingsInput.value);

    const btn = document.getElementById('generateBtn');
    const status = document.getElementById('status');
    const errorBox = document.getElementById('errorBox');
    const results = document.getElementById('results');
    const recipeGrid = document.getElementById('recipeGrid');

    btn.addEventListener('click', async () => {
      const ingredients = document.getElementById('ingredients').value.trim();
      const diet = document.getElementById('diet').value;
      const cuisine = document.getElementById('cuisine').value;
      const servings = servingsInput.value;
      const modelField = document.getElementById('modelField');
      const model = modelField && modelField.value.trim() ? modelField.value.trim() : null;

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
          recipeGrid.innerHTML = lastRecipes.map(recipeCardHTML).join('');
          results.style.display = 'block';
        }
      } catch (err) {
        errorBox.textContent = 'Could not reach the server: ' + err.message;
        errorBox.style.display = 'block';
      } finally {
        btn.disabled = false;
        status.style.display = 'none';
      }
    });

    recipeGrid.addEventListener('click', async (e) => {
      const saveBtn = e.target.closest('.save-btn');
      if (!saveBtn || saveBtn.classList.contains('saved')) return;

      const idx = Number(saveBtn.dataset.index);
      const recipe = lastRecipes[idx];
      if (!recipe) return;

      saveBtn.disabled = true;
      try {
        const res = await fetch('/favorites', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recipe, model_used: lastModelUsed })
        });
        if (res.ok) {
          saveBtn.textContent = '★ Saved';
          saveBtn.classList.add('saved');
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

  function initFavorites() {
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
            starGroup.querySelectorAll('.star').forEach(s => {
              s.classList.toggle('filled', Number(s.dataset.value) <= value);
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
            delBtn.closest('.recipe-card').remove();
          } else {
            delBtn.disabled = false;
          }
        } catch {
          delBtn.disabled = false;
        }
      });
    });
  }

  return { initGenerator, initFavorites };
})();
