#!/firmadyne/sh
BUSYBOX=/firmadyne/busybox

# Find the init script in the rootfs
INIT=""
for candidate in /sbin/init /etc/init.d/rcS /bin/init /init; do
    if [ -e "${candidate}" ]; then
        INIT="${candidate}"
        break
    fi
done

if [ -z "${INIT}" ]; then
    # Look for rcS or init in common locations
    for f in /sbin/rcS /etc/rcS /bin/sh; do
        if [ -e "${f}" ]; then
            INIT="${f}"
            break
        fi
    done
fi

echo "${INIT}" > /firmadyne/init