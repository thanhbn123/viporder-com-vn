document.getElementById("year").textContent = new Date().getFullYear();

const form = document.getElementById("registerForm");
const message = document.getElementById("formMessage");

if (form) {
  form.addEventListener("submit", function (event) {
    event.preventDefault();

    message.textContent =
      "Giao diện đăng ký đã sẵn sàng. API tạo mã khách hàng sẽ được kết nối ở G02.";

    message.style.color = "#067647";
  });
}
