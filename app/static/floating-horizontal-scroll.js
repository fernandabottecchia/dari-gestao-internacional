/* v28 — barra horizontal flutuante para tabelas largas.
   A barra aparece no rodapé da janela enquanto o fim real da tabela
   ainda está abaixo da viewport. Ela é sincronizada nos dois sentidos
   com o scroll horizontal original da tabela. */
(() => {
  'use strict';

  const BAR_HEIGHT = 18;
  let activeScroller = null;
  let syncing = false;

  const floating = document.createElement('div');
  floating.className = 'floating-horizontal-scroll';
  floating.setAttribute('aria-hidden', 'true');

  const spacer = document.createElement('div');
  spacer.className = 'floating-horizontal-scroll-spacer';
  floating.appendChild(spacer);
  document.body.appendChild(floating);

  function overflowXAllowsScroll(element) {
    const style = getComputedStyle(element);
    return style.overflowX === 'auto' || style.overflowX === 'scroll';
  }

  function findScrollableAncestor(table) {
    let element = table.parentElement;
    while (element && element !== document.body) {
      if (
        overflowXAllowsScroll(element) &&
        element.scrollWidth > element.clientWidth + 2
      ) {
        return element;
      }
      element = element.parentElement;
    }
    return null;
  }

  function collectScrollers() {
    const result = [];
    const seen = new Set();

    document.querySelectorAll('table').forEach((table) => {
      const scroller = findScrollableAncestor(table);
      if (scroller && !seen.has(scroller)) {
        seen.add(scroller);
        result.push(scroller);
      }
    });

    return result;
  }

  function chooseActiveScroller() {
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;

    const candidates = collectScrollers().filter((scroller) => {
      const rect = scroller.getBoundingClientRect();

      if (scroller.scrollWidth <= scroller.clientWidth + 2) return false;
      if (rect.right <= 0 || rect.left >= viewportWidth) return false;

      // A tabela precisa estar alcançando a viewport.
      if (rect.top >= viewportHeight - BAR_HEIGHT) return false;
      if (rect.bottom <= BAR_HEIGHT) return false;

      // Se o fim real da tabela já está visível, a própria barra nativa
      // já pode ser usada; a flutuante deixa de ser necessária.
      if (rect.bottom <= viewportHeight) return false;

      return true;
    });

    if (!candidates.length) return null;

    // Se houver mais de uma tabela parcialmente visível, usamos a mais
    // próxima da zona central/inferior da tela.
    candidates.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      return Math.abs(ra.top) - Math.abs(rb.top);
    });

    return candidates[0];
  }

  function syncFloatingFromSource() {
    if (!activeScroller || syncing) return;
    syncing = true;
    floating.scrollLeft = activeScroller.scrollLeft;
    syncing = false;
  }

  function detachActive() {
    if (activeScroller) {
      activeScroller.removeEventListener('scroll', syncFloatingFromSource);
    }
    activeScroller = null;
  }

  function attachActive(scroller) {
    if (activeScroller === scroller) return;

    detachActive();
    activeScroller = scroller;

    if (activeScroller) {
      activeScroller.addEventListener('scroll', syncFloatingFromSource, { passive: true });
    }
  }

  function update() {
    const scroller = chooseActiveScroller();
    attachActive(scroller);

    if (!activeScroller) {
      floating.classList.remove('is-visible');
      return;
    }

    const rect = activeScroller.getBoundingClientRect();
    const left = Math.max(0, rect.left);
    const right = Math.min(window.innerWidth, rect.right);
    const width = Math.max(0, right - left);

    if (width < 40) {
      floating.classList.remove('is-visible');
      return;
    }

    floating.style.left = `${left}px`;
    floating.style.width = `${width}px`;
    spacer.style.width = `${activeScroller.scrollWidth}px`;

    syncFloatingFromSource();
    floating.classList.add('is-visible');
  }

  floating.addEventListener('scroll', () => {
    if (!activeScroller || syncing) return;
    syncing = true;
    activeScroller.scrollLeft = floating.scrollLeft;
    syncing = false;
  }, { passive: true });

  let scheduled = false;
  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      update();
    });
  }

  window.addEventListener('scroll', scheduleUpdate, { passive: true });
  window.addEventListener('resize', scheduleUpdate, { passive: true });
  window.addEventListener('load', scheduleUpdate);

  const observer = new MutationObserver(scheduleUpdate);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class', 'style']
  });

  if ('ResizeObserver' in window) {
    const resizeObserver = new ResizeObserver(scheduleUpdate);
    resizeObserver.observe(document.documentElement);
  }

  scheduleUpdate();
})();
