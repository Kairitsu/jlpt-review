cmake_minimum_required(VERSION 3.14)
project(runner LANGUAGES CXX)

function(apply_standard_settings target)
  target_compile_features(${target} PUBLIC cxx_std_17)
  target_compile_options(${target} PRIVATE /W4 /WX /wd4100)
  target_compile_options(${target} PRIVATE /EHsc)
  target_compile_definitions(${target} PRIVATE "_HAS_EXCEPTIONS=0")
  target_compile_definitions(${target} PRIVATE "$<$<CONFIG:Debug>:_DEBUG>")
endfunction()

add_executable(${BINARY_NAME} WIN32
  "flutter_window.cpp"
  "main.cpp"
  "utils.cpp"
  "win32_window.cpp"
  "runner.exe.manifest")

apply_standard_settings(${BINARY_NAME})
target_compile_definitions(${BINARY_NAME} PRIVATE "NOMINMAX")
target_link_libraries(${BINARY_NAME} PRIVATE flutter flutter_wrapper_app)

target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_SOURCE_DIR}")
add_dependencies(${BINARY_NAME} flutter_assemble)
