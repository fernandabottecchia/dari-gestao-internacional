/* Configuração de tabelas do MVP 0.8. Não altera os registros do banco. */
(() => {
  'use strict';
  const PREFIX = 'dari-mvp-v08-columns:';
  const storage = {
    read(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; } },
    save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} },
    remove(key) { try { localStorage.removeItem(key); } catch (_) {} }
  };
  const widgets = Array.from(document.querySelectorAll('[data-table-widget]'));
  widgets.forEach((widget) => {
    const table = widget.querySelector('[data-column-table]');
    const chooser = widget.querySelector('[data-column-options]');
    const count = widget.querySelector('[data-column-count]');
    const restore = widget.querySelector('[data-restore-columns]');
    const visibleExport = widget.querySelector('[data-export-visible]');
    const baseExportUrl = visibleExport?.getAttribute('href');
    if (!table || !chooser || !table.tHead || !table.tHead.rows.length) return;
    const heads = Array.from(table.tHead.rows[0].cells);
    const indexes = heads.map((th, i) => ({th, i})).filter(({th}) => !th.dataset.fixedColumn);
    if (!indexes.length) return;
    const key = PREFIX + table.dataset.columnTable;
    const saved = storage.read(key);
    const allowed = new Set(indexes.map(({i}) => i));
    let current = Array.isArray(saved) ? saved.filter(i => Number.isInteger(i) && allowed.has(i)) : [...allowed];
    if (!current.length) current = [...allowed];
    const checkboxes = new Map();
    const update = (persist=true) => {
      const visible = new Set(current);
      heads.forEach((th, i) => {
        if (th.dataset.fixedColumn) return;
        th.classList.toggle('is-column-hidden', !visible.has(i));
      });
      if (table.tBodies.length) {
        Array.from(table.tBodies[0].rows).forEach(row => {
          if (row.cells.length === 1 && row.cells[0].hasAttribute('colspan')) {
            row.cells[0].colSpan = visible.size + heads.filter(th => th.dataset.fixedColumn).length;
          } else {
            Array.from(row.cells).forEach((cell, i) => {
              if (!heads[i] || heads[i].dataset.fixedColumn) return;
              cell.classList.toggle('is-column-hidden', !visible.has(i));
            });
          }
        });
      }
      checkboxes.forEach((checkbox, i) => {
        checkbox.checked = visible.has(i);
        checkbox.disabled = visible.size === 1 && visible.has(i);
      });
      count.textContent = `${visible.size} de ${indexes.length} colunas`;
      if (visibleExport) {
        try {
          const u = new URL(baseExportUrl, window.location.href);
          u.searchParams.set('columns', indexes.filter(({i}) => visible.has(i)).map(({i}) => i).join(','));
          visibleExport.href = u.toString();
        } catch (_) { visibleExport.href = baseExportUrl || ''; }
      }
      if (persist) storage.save(key, indexes.filter(({i}) => visible.has(i)).map(({i}) => i));
    };
    indexes.forEach(({th,i}) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.checked = current.includes(i);
      input.setAttribute('aria-label', 'Exibir coluna ' + th.textContent.trim());
      label.append(input, document.createTextNode(th.textContent.trim()));
      chooser.append(label);
      checkboxes.set(i, input);
      input.addEventListener('change', () => {
        const selected = new Set(current);
        if (input.checked) selected.add(i); else selected.delete(i);
        if (!selected.size) { input.checked = true; return; }
        current = indexes.map(({i}) => i).filter(i => selected.has(i));
        update();
      });
    });
    restore.addEventListener('click', () => {current = indexes.map(({i}) => i);storage.remove(key);update(false);});
    update(false);
  });
})();
