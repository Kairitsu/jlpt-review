#include "utils.h"

#include <windows.h>

#include <iostream>

namespace {
std::string Utf8FromUtf16(const wchar_t* utf16_string) {
  if (utf16_string == nullptr) {
    return std::string();
  }
  int target_length = WideCharToMultiByte(CP_UTF8, 0, utf16_string, -1, nullptr,
                                          0, nullptr, nullptr);
  std::string utf8_string(target_length - 1, '\0');
  WideCharToMultiByte(CP_UTF8, 0, utf16_string, -1, utf8_string.data(),
                      target_length, nullptr, nullptr);
  return utf8_string;
}
}  // namespace

void CreateAndAttachConsole() {
  if (::AllocConsole()) {
    FILE* unused;
    freopen_s(&unused, "CONOUT$", "w", stdout);
    freopen_s(&unused, "CONOUT$", "w", stderr);
    freopen_s(&unused, "CONIN$", "r", stdin);
    std::ios::sync_with_stdio();
  }
}

std::vector<std::string> GetCommandLineArguments() {
  int argc;
  wchar_t** argv = ::CommandLineToArgvW(::GetCommandLineW(), &argc);
  if (argv == nullptr) {
    return std::vector<std::string>();
  }

  std::vector<std::string> command_line_arguments;
  for (int i = 1; i < argc; i++) {
    command_line_arguments.push_back(Utf8FromUtf16(argv[i]));
  }
  ::LocalFree(argv);
  return command_line_arguments;
}
