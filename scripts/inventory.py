"""Inventory every file in data/raw -> outputs/inventory.csv + outputs/inventory.md."""
from tubesense.ingest import ROOT
from tubesense.inventory import inventory, to_markdown

if __name__ == "__main__":
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    df = inventory()
    df.to_csv(out / "inventory.csv", index=False)
    (out / "inventory.md").write_text(to_markdown(df), encoding="utf-8")
    print(df[["stem", "sensor_dur_s", "wall_span_s", "audio_dur_s", "n_pauses", "acc_n_gaps", "mag_B_med_uT"]].to_string())
