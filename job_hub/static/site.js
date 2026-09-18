document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector(".nav-toggle");
  const navigation = document.querySelector(".primary-nav");

  if (!button || !navigation) {
    return;
  }

  button.addEventListener("click", () => {
    const isOpen = navigation.classList.toggle("is-open");
    button.setAttribute("aria-expanded", String(isOpen));
  });

  navigation.addEventListener("click", () => {
    navigation.classList.remove("is-open");
    button.setAttribute("aria-expanded", "false");
  });
});
