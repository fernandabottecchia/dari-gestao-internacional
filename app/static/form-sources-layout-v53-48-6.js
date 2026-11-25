/* DARI v53.48.6 — expansão e cancelamento sem envio de dados. */
(function () {
  'use strict';
  var page = document.querySelector('[data-dari-form-sources]');
  if (!page) return;

  function setExpanded(id, show) {
    var panel = document.getElementById(id);
    if (!panel) return;
    panel.hidden = !show;
    page.querySelectorAll('[data-fsrc-toggle]').forEach(function (button) {
      if (button.getAttribute('data-fsrc-toggle') !== id) return;
      button.setAttribute('aria-expanded', show ? 'true' : 'false');
      if (button.classList.contains('dari-fsrc-edit-toggle')) {
        // A edição é encerrada exclusivamente pelo botão Cancelar no rodapé.
        // Ocultar o botão superior não descarta dados: Cancelar reinicia o form.
        button.textContent = 'Editar';
        button.style.display = show ? 'none' : '';
      }
      else if (button.textContent.trim() === 'Configurar' || button.textContent.trim() === 'Fechar')
        button.textContent = show ? 'Fechar' : 'Configurar';
    });
  }

  page.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.matches('[data-fsrc-archive]')) return;
    var name = form.getAttribute('data-fsrc-archive') || 'esta planilha';
    if (!window.confirm('Excluir o formulário "' + name + '" da configuração?\n\nA leitura será interrompida. Os dados e documentos já associados aos estudantes serão preservados.')) {
      event.preventDefault();
    }
  });

  page.addEventListener('click', function (event) {
    var toggle = event.target.closest('[data-fsrc-toggle]');
    if (toggle && page.contains(toggle)) {
      var id = toggle.getAttribute('data-fsrc-toggle');
      var panel = document.getElementById(id);
      if (panel) setExpanded(id, panel.hidden);
      return;
    }
    var cancel = event.target.closest('[data-fsrc-cancel]');
    if (!cancel || !page.contains(cancel)) return;
    var targetId = cancel.getAttribute('data-fsrc-cancel');
    var target = document.getElementById(targetId);
    if (!target) return;
    var form = target.matches('form') ? target : target.querySelector('form');
    if (form) form.reset();
    target.querySelectorAll('details[open]').forEach(function (item) { item.open = false; });
    setExpanded(targetId, false);
  });
})();
