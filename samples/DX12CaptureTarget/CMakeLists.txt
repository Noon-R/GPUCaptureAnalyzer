cmake_minimum_required(VERSION 3.20)
project(DX12CaptureTarget CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(DX12CaptureTarget main.cpp)
target_compile_definitions(DX12CaptureTarget PRIVATE UNICODE _UNICODE)
target_compile_options(DX12CaptureTarget PRIVATE /W4 /utf-8 /wd4100)
target_link_libraries(DX12CaptureTarget PRIVATE d3d12 dxgi d3dcompiler shell32)
set_target_properties(DX12CaptureTarget PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/../../bin"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_SOURCE_DIR}/../../bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_SOURCE_DIR}/../../bin")
