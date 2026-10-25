const links = [...document.querySelectorAll('.nav-link')];
const sections = [...document.querySelectorAll('.section-anchor')];
function updateNavigation() {
  const current = [...sections].reverse().find(section => section.getBoundingClientRect().top <= 150) || sections[0];
  for (const link of links) {
    const selected = link.hash === `#${current.id}`;
    link.classList.toggle('active', selected);
    if (selected) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
}
window.addEventListener('scroll', updateNavigation, { passive: true });
updateNavigation();
document.querySelector('#copy-checksum').addEventListener('click', async () => {
  const status = document.querySelector('#copy-status');
  const checksum = document.querySelector('#checksum');
  try {
    await navigator.clipboard.writeText(checksum.textContent.trim());
    status.textContent = 'Checksum copied.';
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(checksum);
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = 'Checksum selected. Press Ctrl+C (or copy from the selection menu).';
  }
});
