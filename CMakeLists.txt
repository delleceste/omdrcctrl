cmake_minimum_required(VERSION 3.16)
project(omdrcctrl VERSION 1.0.0 LANGUAGES NONE)

# ── options ────────────────────────────────────────────────────────────────
option(USER_INSTALL "Install as a systemd --user service on Linux (no root required)" OFF)
set(OMDRCCTRL_SERVICE_USER "$ENV{USER}" CACHE STRING "User for the system service")

if(NOT OMDRCCTRL_SERVICE_USER)
    set(OMDRCCTRL_SERVICE_USER "nobody")
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(OMDRCCTRL_SERVICE_MANAGER "systemd")
elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    set(OMDRCCTRL_SERVICE_MANAGER "rc.d")
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
    src/omdrcctrl.sh.in
    ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl
    @ONLY
)

if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        configure_file(
            systemd/omdrcctrl-user.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
            @ONLY
        )
    else()
        configure_file(
            systemd/omdrcctrl.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
            @ONLY
        )
    endif()
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    configure_file(
        rc.d/omdrcctrl.in
        ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
        @ONLY
    )
endif()

# ── install targets ────────────────────────────────────────────────────────

# Python application + docs + helper scripts
install(FILES src/app.py README.md
        DESTINATION lib/omdrcctrl)

# Jinja2 templates
install(DIRECTORY src/templates
        DESTINATION lib/omdrcctrl)

# Static assets (vendored Chart.js for the filter-response page)
install(DIRECTORY src/static
        DESTINATION lib/omdrcctrl)

# Default config (only if not already present — preserve user edits)
install(FILES src/commands.conf
        DESTINATION etc/omdrcctrl
        RENAME commands.conf)

# Launcher script
install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl
        DESTINATION bin)

# service integration
if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
                DESTINATION share/systemd/user)
    else()
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
                DESTINATION lib/systemd/system)
    endif()
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
            DESTINATION etc/rc.d
            RENAME omdrcctrl)
endif()

# ── post-install hints ─────────────────────────────────────────────────────
if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd" AND USER_INSTALL)
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service (no sudo required):\")
message(STATUS \"  systemctl --user daemon-reload\")
message(STATUS \"  systemctl --user enable --now omdrcctrl\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo systemctl daemon-reload\")
message(STATUS \"  sudo systemctl enable --now omdrcctrl\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo sysrc omdrcctrl_enable=YES\")
message(STATUS \"  sudo service omdrcctrl start\")
message(STATUS \"\")
message(STATUS \"Optional service settings:\")
message(STATUS \"  sudo sysrc omdrcctrl_user=${OMDRCCTRL_SERVICE_USER}\")
message(STATUS \"  sudo sysrc omdrcctrl_env=DISPLAY=:0\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:8080\")
")
endif()
