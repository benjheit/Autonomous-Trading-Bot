import json
import os

UNIVERSE_FILE = "universe.json"

def pardon_all_stocks():
    if not os.path.exists(UNIVERSE_FILE):
        print("❌ No universe.json found!")
        return

    try:
        with open(UNIVERSE_FILE, 'r') as f:
            universe = json.load(f)
        
        count = 0
        print("\n--- ⚕️ STARTING MASS RESURRECTION ---")
        
        for symbol, data in universe.items():
            # If stock is "dead" (Score < 50) or "injured" (Score < 100)
            if data["score"] < 100:
                old_score = data["score"]
                # RESET TO 100 (Full Pardon)
                universe[symbol]["score"] = 100
                print(f"   ✨ Resurrected {symbol}: Score {old_score} -> 100")
                count += 1
                
        with open(UNIVERSE_FILE, 'w') as f:
            json.dump(universe, f, indent=4)
            
        print(f"--- ✅ COMPLETE: {count} stocks pardoned. Restart your bot now. ---\n")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    pardon_all_stocks()