// Hash routes work with browser back/forward and links copied to another tab.
function showSection() {
  const subdomains = location.hash === '#gateway-section';
  document.querySelector('#connections').hidden = subdomains;
  document.querySelector('.stats').hidden = subdomains;
  document.querySelector('.page-heading').hidden = subdomains;
  document.querySelector('#gateway-section').hidden = !subdomains;
  document.querySelector('#section-name').textContent = subdomains ? 'Subdomæner' : 'Forbindelser';
  document.title = 'FjordVPN · ' + (subdomains ? 'Subdomæner' : 'Forbindelser');
  for (const link of document.querySelectorAll('[data-section-link]')) {
    const active = link.hash === (subdomains ? '#gateway-section' : '#connections');
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}
window.addEventListener('hashchange', showSection);
showSection();
