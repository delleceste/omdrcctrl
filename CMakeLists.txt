cmake_minimum_required(VERSION 3.16)
project(arkictrl VERSION 1.0.0 LANGUAGES NONE)

# ── options ────────────────────────────────────────────────────────────────
option(USER_INSTALL "Install as a systemd --user service on Linux (no root required)" OFF)
set(ARKICTRL_SERVICE_USER "$ENV{USER}" CACHE STRING "User for the system service")

if(NOT ARKICTRL_SERVICE_USER)
    set(ARKICTRL_SERVICE_USER "nobody")
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(ARKICTRL_SERVICE_MANAGER "systemd")
elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    set(ARKICTRL_SERVICE_MANAGER "rc.d")
    if(USER_INSTALL)
        message(FATAL_ERROR "USER_INSTALL is only supported on Linux with systemd --user services")
    endif()
else()
    message(FATAL_ERROR "Unsupported system: ${CMAKE_SYSTEM_NAME}. Supported systems: Linux, FreeBSD")
endif()

if(USER_INSTALL)
    set(CMAKE_INSTALL_PREFIX "$ENV{HOME}/.local" CACHE PATH "Installation prefix" FORCE)
endif()

# ── check Python 3 ────────────────────────────────────────────────────────
find_program(PYTHON3 python3 REQUIRED)

execute_process(
    COMMAND ${PYTHON3} -c "import flask"
    RESULT_VARIABLE _flask_rc
    OUTPUT_QUIET ERROR_QUIET
)
if(NOT _flask_rc EQUAL 0)
    message(WARNING "Flask is not installed. Run:  pip install flask")
endif()

# ── configure files ────────────────────────────────────────────────────────
configure_file(
    src/arkictrl.sh.in
    ${CMAKE_CURRENT_BINARY_DIR}/arkictrl
    @ONLY
)

if(ARKICTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        configure_file(
            systemd/arkictrl-user.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
            @ONLY
        )
    else()
        configure_file(
            systemd/arkictrl.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
            @ONLY
        )
    endif()
elseif(ARKICTRL_SERVICE_MANAGER STREQUAL "rc.d")
    configure_file(
        rc.d/arkictrl.in
        ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.rc
        @ONLY
    )
endif()

# ── install targets ────────────────────────────────────────────────────────

# Python application + docs + helper scripts
install(FILES src/app.py README.md
        DESTINATION lib/arkictrl)

# Jinja2 templates
install(DIRECTORY src/templates
        DESTINATION lib/arkictrl)

# Static assets (vendored Chart.js for the filter-response page)
install(DIRECTORY src/static
        DESTINATION lib/arkictrl)

# Default config (only if not already present — preserve user edits)
install(FILES src/commands.conf
        DESTINATION etc/arkictrl
        RENAME commands.conf)

# Launcher script
install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/arkictrl
        DESTINATION bin)

# service integration
if(ARKICTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
                DESTINATION share/systemd/user)
    else()
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
                DESTINATION lib/systemd/system)
    endif()
elseif(ARKICTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.rc
            DESTINATION etc/rc.d
            RENAME arkictrl)
endif()

# ── post-install hints ─────────────────────────────────────────────────────
if(ARKICTRL_SERVICE_MANAGER STREQUAL "systemd" AND USER_INSTALL)
    install(CODE "
message(STATUS \"\")
message(STATUS \"arkictrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/arkictrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service (no sudo required):\")
message(STATUS \"  systemctl --user daemon-reload\")
message(STATUS \"  systemctl --user enable --now arkictrl\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
elseif(ARKICTRL_SERVICE_MANAGER STREQUAL "systemd")
    install(CODE "
message(STATUS \"\")
message(STATUS \"arkictrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/arkictrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo systemctl daemon-reload\")
message(STATUS \"  sudo systemctl enable --now arkictrl\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
elseif(ARKICTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(CODE "
message(STATUS \"\")
message(STATUS \"arkictrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/arkictrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo sysrc arkictrl_enable=YES\")
message(STATUS \"  sudo service arkictrl start\")
message(STATUS \"\")
message(STATUS \"Optional service settings:\")
message(STATUS \"  sudo sysrc arkictrl_user=${ARKICTRL_SERVICE_USER}\")
message(STATUS \"  sudo sysrc arkictrl_env=DISPLAY=:0\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
endif()
