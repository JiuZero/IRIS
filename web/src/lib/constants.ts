/** Numbers the server is the authority for, kept here so the workbench stops
 *  carrying private copies of them.
 *
 *  The boot timeout is the one that mattered: this file used to hold `200` in the
 *  launch dialog while the API and the CLI both said `120`, so the same firmware
 *  launched from the workbench and from the command line got a different amount
 *  of patience and neither difference was written down anywhere. Both were under
 *  the slowest boot this corpus has actually completed (an arm64 TES7002 took 420s
 *  in three separate successful runs), so both were reporting failures on a timer.
 *
 *  `iris.config.DEFAULT_BOOT_TIMEOUT_SEC` is the same number and the source of
 *  truth; tests/test_boot_timeout_default.py asserts the two agree. Change one and
 *  the other fails loudly. */

/** Seconds a guest gets to answer on its web port before the run is called failed.
 *  A default, not a ceiling -- the field stays editable. */
export const DEFAULT_BOOT_TIMEOUT_SEC = 600