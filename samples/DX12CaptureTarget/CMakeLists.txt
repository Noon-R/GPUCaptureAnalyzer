cmake_minimum_required(VERSION 3.20)
project(DX12CaptureTarget CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(BIN_DIR "${CMAKE_SOURCE_DIR}/../../bin")
set(SHADER_OUT "${BIN_DIR}/shaders")

add_executable(DX12CaptureTarget main.cpp extras.cpp extras.h)
target_compile_definitions(DX12CaptureTarget PRIVATE UNICODE _UNICODE)
target_compile_options(DX12CaptureTarget PRIVATE /W4 /utf-8 /wd4100)
target_link_libraries(DX12CaptureTarget PRIVATE d3d12 dxgi d3dcompiler shell32)
set_target_properties(DX12CaptureTarget PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${BIN_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${BIN_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${BIN_DIR}")

# ---- shaders ----------------------------------------------------------------------------------
# DXBC 用: HLSL ソースをそのまま配置 (実行時に D3DCompile)
# DXIL 用: dxc で SM6.0 にコンパイル。-Zi で debug info を作り -Qstrip_debug で本体から除去、
#          PDB は -Fd <dir>\ で shaders/pdb/<shader hash>.pdb に自動命名して分離する (シンボル照合の検証用)
find_program(DXC_EXE dxc HINTS "$ENV{WindowsSdkVerBinPath}/x64"
             "C:/Program Files (x86)/Windows Kits/10/bin/${CMAKE_SYSTEM_VERSION}/x64"
             "C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64" REQUIRED)
set(SHADER_SRC "${CMAKE_SOURCE_DIR}/shaders")
set(DXIL_OUTPUTS)
foreach(spec "vs;graphics.hlsl;VSMain;vs_6_0" "ps;graphics.hlsl;PSMain;ps_6_0" "cs;compute.hlsl;CSMain;cs_6_0"
             "ext_vs;ext.hlsl;VSMain;vs_6_0" "ext_ps;ext.hlsl;PSMain;ps_6_0" "ext_cs;ext.hlsl;CSMain;cs_6_0"
             "ext_ms;ext.hlsl;MSMain;ms_6_5" "ext_as;ext.hlsl;ASMain;as_6_5" "ext_ms_amp;ext.hlsl;MSMainAmp;ms_6_5")
    list(GET spec 0 name)
    list(GET spec 1 src)
    list(GET spec 2 entry)
    list(GET spec 3 profile)
    add_custom_command(
        OUTPUT "${SHADER_OUT}/${name}.cso"
        COMMAND ${CMAKE_COMMAND} -E make_directory "${SHADER_OUT}/pdb"
        COMMAND "${DXC_EXE}" -nologo -T ${profile} -E ${entry} -Zi -Qstrip_debug
                -Fo "${SHADER_OUT}/${name}.cso" "-Fd" "${SHADER_OUT}/pdb/" "${SHADER_SRC}/${src}"
        DEPENDS "${SHADER_SRC}/${src}"
        COMMENT "dxc ${name} (${profile})")
    list(APPEND DXIL_OUTPUTS "${SHADER_OUT}/${name}.cso")
endforeach()
# DXR ライブラリ (エントリポイントなし)
add_custom_command(
    OUTPUT "${SHADER_OUT}/rt_lib.cso"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${SHADER_OUT}/pdb"
    COMMAND "${DXC_EXE}" -nologo -T lib_6_3 -Zi -Qstrip_debug
            -Fo "${SHADER_OUT}/rt_lib.cso" "-Fd" "${SHADER_OUT}/pdb/" "${SHADER_SRC}/rt.hlsl"
    DEPENDS "${SHADER_SRC}/rt.hlsl"
    COMMENT "dxc rt_lib (lib_6_3)")
list(APPEND DXIL_OUTPUTS "${SHADER_OUT}/rt_lib.cso")
add_custom_target(shaders ALL DEPENDS ${DXIL_OUTPUTS}
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${SHADER_SRC}/graphics.hlsl" "${SHADER_SRC}/compute.hlsl" "${SHADER_OUT}/")
add_dependencies(DX12CaptureTarget shaders)
