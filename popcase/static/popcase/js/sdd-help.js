/* Source text is rendered with textContent, never interpreted as HTML. */
(() => {
  function init() {
    const data = document.getElementById('sdd-help-data');
    if (!data) return;
    const config = JSON.parse(data.textContent);
    if (!config.bindings) return;
    const panel = document.createElement('div');
    panel.id = 'sdd-help-panel';
    panel.className = 'sdd-help-panel';
    panel.setAttribute('role', 'tooltip');
    panel.hidden = true;
    document.body.append(panel);
    let active = null, pinned = false, timer;
    const controls = [];
    function resolve(topic) {
      if (topic === 'bridged_race' && !['county', 'tract', 'none', 'total'].includes(config.geography)) return '';
      if (topic === 'community_rurality') {
        const level = config.geography === 'patient'
          ? document.querySelector('input[name="patient_community_geography"]:checked')?.value || 'tract'
          : config.geography;
        return config.texts[level + '_rurality'] || '';
      }
      return config.texts[topic] || '';
    }
    function hide() {
      clearTimeout(timer);
      if (active) active.removeAttribute('aria-describedby');
      active = null; pinned = false; panel.hidden = true;
    }
    function position() {
      if (!active) return;
      const anchor = active.getBoundingClientRect();
      const box = panel.getBoundingClientRect();
      const left = Math.max(12, Math.min(anchor.left, window.innerWidth - box.width - 12));
      let top = anchor.bottom + 6;
      if (top + box.height > window.innerHeight - 12) top = anchor.top - box.height - 6;
      panel.style.left = left + 'px';
      panel.style.top = Math.max(12, top) + 'px';
    }
    function show(trigger) {
      clearTimeout(timer);
      if (active !== trigger) hide();
      const text = resolve(trigger.dataset.topic);
      if (!text) return;
      active = trigger;
      panel.textContent = text;
      panel.hidden = false;
      trigger.setAttribute('aria-describedby', panel.id);
      position();
    }
    function deferHide() {
      clearTimeout(timer);
      if (!pinned && document.activeElement !== active) timer = setTimeout(hide, 180);
    }
    function attach(target, topic, label) {
      if (!target || target.dataset.sddHelpAttached) return;
      target.dataset.sddHelpAttached = 'true';
      // A focusable span keeps help usable inside disabled patient fieldsets.
      const trigger = document.createElement('span');
      trigger.className = 'sdd-help-trigger';
      trigger.dataset.topic = topic;
      trigger.setAttribute('role', 'button');
      trigger.tabIndex = 0;
      trigger.setAttribute('aria-label', 'About ' + label.trim());
      trigger.textContent = 'ⓘ';
      if (target.hasAttribute('data-sdd-help')) target.append(trigger);
      else target.after(trigger);
      controls.push(trigger);
      trigger.addEventListener('mouseenter', () => show(trigger));
      trigger.addEventListener('mouseleave', deferHide);
      trigger.addEventListener('focus', () => show(trigger));
      trigger.addEventListener('blur', () => { pinned = false; deferHide(); });
      function toggle(event) {
        event.preventDefault(); event.stopPropagation();
        if (active === trigger && pinned) hide();
        else { show(trigger); pinned = true; }
      }
      trigger.addEventListener('click', toggle);
      trigger.addEventListener('keydown', event => {
        if (['Enter', ' '].includes(event.key)) toggle(event);
      });
    }
    config.bindings.forEach(binding => {
      document.querySelectorAll('input[name="' + binding.field + '"]').forEach(input => {
        if (binding.value !== null && input.value !== binding.value) return;
        const label = Array.from(input.labels || []).find(node => node.htmlFor === input.id);
        if (label) attach(label, binding.topic, label.textContent);
      });
    });
    document.querySelectorAll('[data-sdd-help]').forEach(node => attach(node, node.dataset.sddHelp, node.textContent));
    function refresh() {
      hide();
      controls.forEach(trigger => { trigger.hidden = !resolve(trigger.dataset.topic); });
    }
    document.querySelectorAll('input[name="patient_community_geography"]').forEach(input => input.addEventListener('change', refresh));
    panel.addEventListener('mouseenter', () => clearTimeout(timer));
    panel.addEventListener('mouseleave', deferHide);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
    document.addEventListener('click', event => {
      if (!panel.contains(event.target) && !event.target.closest('.sdd-help-trigger')) hide();
    });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
