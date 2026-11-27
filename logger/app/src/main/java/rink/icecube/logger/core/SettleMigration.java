package rink.icecube.logger.core;

/**
 * The one-time move of the fix settle setting (pref fix_settle_s) to the 0.11.0 default
 * (#15): the old default, 10 s, held captures back; from 0.11.0 the same setting is the
 * young-fix warning limit, default 120 s. A stored 10 (the old default, almost certainly
 * never chosen) becomes 120, once: Settings then sets a marker pref, so a 10 chosen later
 * on purpose stays. Any other stored value, or none, is kept.
 */
public final class SettleMigration {
    /** The pre-0.11.0 default that is moved. */
    public static final String OLD_DEFAULT = "10";

    private SettleMigration() { }

    /**
     * The text to store for fix_settle_s: storedText (null when unset) unless the migration
     * has not run yet and it holds the old default, then the new default ("120").
     */
    public static String migrate(String storedText, boolean alreadyMigrated) {
        if (alreadyMigrated || storedText == null) return storedText;
        if (OLD_DEFAULT.equals(storedText.trim())) return Limit.SETTLE_S.format(Limit.SETTLE_S.def);
        return storedText;
    }
}
