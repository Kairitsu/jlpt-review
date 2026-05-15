#include "win32_window.h"

namespace {
constexpr wchar_t kWindowClassName[] = L"FLUTTER_RUNNER_WIN32_WINDOW";

LRESULT CALLBACK WndProc(HWND const window, UINT const message,
                         WPARAM const wparam, LPARAM const lparam) noexcept {
  if (message == WM_NCCREATE) {
    auto window_struct = reinterpret_cast<CREATESTRUCT*>(lparam);
    SetWindowLongPtr(window, GWLP_USERDATA,
                     reinterpret_cast<LONG_PTR>(window_struct->lpCreateParams));
  }

  auto that = reinterpret_cast<Win32Window*>(GetWindowLongPtr(window, GWLP_USERDATA));
  if (that != nullptr) {
    return that->MessageHandler(window, message, wparam, lparam);
  }
  return DefWindowProc(window, message, wparam, lparam);
}
}  // namespace

Win32Window::Win32Window() {}

Win32Window::~Win32Window() {
  if (window_handle_) {
    DestroyWindow(window_handle_);
  }
}

bool Win32Window::Create(const std::wstring& title, const Point& origin,
                         const Size& size) {
  WNDCLASS window_class{};
  window_class.hCursor = LoadCursor(nullptr, IDC_ARROW);
  window_class.lpszClassName = kWindowClassName;
  window_class.style = CS_HREDRAW | CS_VREDRAW;
  window_class.cbClsExtra = 0;
  window_class.cbWndExtra = 0;
  window_class.hInstance = GetModuleHandle(nullptr);
  window_class.hIcon = LoadIcon(window_class.hInstance, MAKEINTRESOURCE(101));
  window_class.hbrBackground = 0;
  window_class.lpszMenuName = nullptr;
  window_class.lpfnWndProc = WndProc;
  RegisterClass(&window_class);

  DWORD window_style = WS_OVERLAPPEDWINDOW;
  RECT frame = {static_cast<LONG>(origin.x), static_cast<LONG>(origin.y),
                static_cast<LONG>(origin.x + size.width),
                static_cast<LONG>(origin.y + size.height)};
  AdjustWindowRect(&frame, window_style, FALSE);

  window_handle_ = CreateWindow(kWindowClassName, title.c_str(), window_style,
                                frame.left, frame.top, frame.right - frame.left,
                                frame.bottom - frame.top, nullptr, nullptr,
                                GetModuleHandle(nullptr), this);
  return window_handle_ != nullptr && OnCreate();
}

void Win32Window::Show() { ShowWindow(window_handle_, SW_SHOWNORMAL); }

void Win32Window::SetQuitOnClose(bool quit_on_close) { quit_on_close_ = quit_on_close; }

HWND Win32Window::GetHandle() { return window_handle_; }

RECT Win32Window::GetClientArea() {
  RECT frame;
  GetClientRect(window_handle_, &frame);
  return frame;
}

void Win32Window::SetChildContent(HWND content) {
  child_content_ = content;
  SetParent(content, window_handle_);
  RECT frame = GetClientArea();
  MoveWindow(content, frame.left, frame.top, frame.right - frame.left,
             frame.bottom - frame.top, TRUE);
}

bool Win32Window::OnCreate() { return true; }

void Win32Window::OnDestroy() {}

LRESULT Win32Window::MessageHandler(HWND window, UINT const message,
                                    WPARAM const wparam,
                                    LPARAM const lparam) noexcept {
  switch (message) {
    case WM_DESTROY:
      OnDestroy();
      if (quit_on_close_) {
        PostQuitMessage(0);
      }
      return 0;
    case WM_SIZE:
      if (child_content_) {
        RECT frame = GetClientArea();
        MoveWindow(child_content_, frame.left, frame.top, frame.right - frame.left,
                   frame.bottom - frame.top, TRUE);
      }
      return 0;
  }
  return DefWindowProc(window, message, wparam, lparam);
}
