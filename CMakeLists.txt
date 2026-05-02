cmake_minimum_required(VERSION 3.16)
project(arkictrl VERSION 1.0.0 LANGUAGES NONE)

# ── install prefix default ─────────────────────────────────────────────────
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
    set(CMAKE_INSTALL_PREFIX "/usr/local" CACHE PATH "Installation prefix" FORCE)
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
configure_file(
    systemd/arkictrl.service.in
    ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
    @ONLY
)

# ── install targets ────────────────────────────────────────────────────────

# Python application
install(FILES src/app.py
        DESTINATION lib/arkictrl)

# Jinja2 templates
install(DIRECTORY src/templates
        DESTINATION lib/arkictrl)

# Default config (only if not already present — preserve user edits)
install(FILES src/commands.conf
        DESTINATION etc/arkictrl
        RENAME commands.conf)

# Launcher script
install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/arkictrl
        DESTINATION bin)

# systemd service unit
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/arkictrl.service
        DESTINATION lib/systemd/system)

# ── post-install hints ─────────────────────────────────────────────────────
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
