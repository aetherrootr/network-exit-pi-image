# Only interactive logins enter setup. Remote commands, SFTP and batch jobs
# remain non-interactive; the root-owned controller validates setup state.
case $- in
    *i*)
        if [ "$(id -u)" = 1000 ] && [ -t 0 ] && [ -t 1 ]; then
            /usr/lib/network-exit/login-bootstrap || exit 1
        fi
        ;;
esac
