/* DARI v53.48.10: documentos sem recarregar a ficha em edição. */
(() => {
  const region = document.getElementById('dari-documents-edit-region');
  if (!region) return;
  const refreshUrl = region.dataset.refreshUrl;
  region.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches('[data-dari-doc-form]')) return;
    // O onsubmit da exclusao trata a confirmacao (tambem sem JavaScript).
    if (event.defaultPrevented) return;
    event.preventDefault();
    const controls = Array.from(form.querySelectorAll('button[type="submit"]'));
    controls.forEach((button) => {button.disabled = true;});
    const feedback = region.querySelector('.dari-doc-feedback');
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'Accept': 'application/json'}
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível salvar o documento.');
      }
      const result = await response.json();
      if (!result.ok) throw new Error('Não foi possível concluir a operação.');
      const refreshed = await fetch(refreshUrl, {credentials:'same-origin', headers:{'Accept':'text/html'}});
      if (!refreshed.ok) throw new Error('Documento salvo, mas não foi possível atualizar a listagem.');
      const markup = await refreshed.text();
      region.innerHTML = markup;
    } catch (error) {
      if (feedback) {
        feedback.textContent = error instanceof Error ? error.message : 'Não foi possível concluir a operação.';
        feedback.hidden = false;
      }
    } finally {
      controls.forEach((button) => {button.disabled = false;});
    }
  });
})();
