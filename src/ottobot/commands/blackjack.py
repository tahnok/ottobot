# ==============================================================================
# VERSION: 0.0.4
# CHANGELOG: 
# - Incremented patch version to 0.0.4.
# - Implemented the "UI Divider" layout to visually isolate the dealer's inline 
#   cards from the player's ASCII cards.
# - Added spacing to the dealer's inline cards for better readability.
# ==============================================================================

"""!blackjack — Play a hand of Blackjack against Ottobot."""
import random
from ottobot import Context, command

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
# Stores active games. Key: user identifier, Value: game state dict
ACTIVE_GAMES = {}

SUITS = ['♠', '♥', '♦', '♣']
VALUES = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']

def calculate_score(hand):
    """Calculates the best possible score for a hand."""
    score = 0
    aces = 0
    for v, s in hand:
        if v in ['J', 'Q', 'K']:
            score += 10
        elif v == 'A':
            aces += 1
            score += 11
        else:
            score += int(v)
    
    # Downgrade aces from 11 to 1 if busting
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score

def draw_inline_hand(hand, hide_second=False):
    """Generates an ultra-compact inline string for the dealer's hand."""
    res = []
    for i, (v, s) in enumerate(hand):
        if hide_second and i == 1:
            res.append("[ ?? ]")
        else:
            val_str = f"{v}{s}" if v == '10' else f"{v} {s}"
            res.append(f"[ {val_str} ]")
    return "  ".join(res)

def draw_ascii_hand(hand):
    """Generates a compact 3-line ASCII representation of a hand."""
    line1, line2, line3 = "", "", ""
    for v, s in hand:
        # Pad single characters with a space so 'A ♠' matches the width of '10♠'
        val_str = f"{v} {s}" if v != '10' else f"{v}{s}"
        line1 += "┌───┐ "
        line2 += f"│{val_str}│ "
        line3 += "└───┘ "
            
    return f"{line1.strip()}\n{line2.strip()}\n{line3.strip()}"

# ==============================================================================
# COMMAND EXECUTION
# ==============================================================================
@command("blackjack", help="Play Blackjack: !blackjack, !blackjack h, !blackjack s")
async def blackjack(ctx: Context) -> str:
    who = ctx.sender_name or "you"
    
    # Safely parse arguments whether ctx.args is a string or a list/tuple
    if hasattr(ctx, 'args') and ctx.args:
        if isinstance(ctx.args, str):
            action = ctx.args.strip().lower()
        else:
            action = " ".join(str(a) for a in ctx.args).strip().lower()
    else:
        action = ""
        
    # Start a new game if no action is provided
    if not action or action == "start":
        if who in ACTIVE_GAMES:
            return f"@[{who}] Game in progress. Type '!blackjack h' or '!blackjack s'."
            
        deck = [(v, s) for v in VALUES for s in SUITS]
        random.shuffle(deck)
        
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]
        
        ACTIVE_GAMES[who] = {
            'deck': deck,
            'player': player_hand,
            'dealer': dealer_hand
        }
        
        p_score = calculate_score(player_hand)
        if p_score == 21:
            del ACTIVE_GAMES[who]
            hand_str = draw_ascii_hand(player_hand)
            return f"@[{who}] BLACKJACK! You win!\n{hand_str}\nScore: 21"
            
        p_ascii = draw_ascii_hand(player_hand)
        d_inline = draw_inline_hand(dealer_hand, hide_second=True)
        
        return (f"@[{who}] DEALER:\n"
                f"{d_inline}\n"
                f"═════════════════\n"
                f"YOU ({p_score}):\n{p_ascii}\n"
                f"!blackjack h/s")

    # Intercept commands if they don't have a game running
    if who not in ACTIVE_GAMES:
        return f"@[{who}] No active game. Type '!blackjack' to deal."

    game = ACTIVE_GAMES[who]
    deck = game['deck']
    player_hand = game['player']
    dealer_hand = game['dealer']

    # Handle 'Hit'
    if action in ["h", "hit"]:
        player_hand.append(deck.pop())
        p_score = calculate_score(player_hand)
        
        if p_score > 21:
            del ACTIVE_GAMES[who]
            p_ascii = draw_ascii_hand(player_hand)
            d_inline = draw_inline_hand(dealer_hand, hide_second=False)
            d_score = calculate_score(dealer_hand)
            
            return (f"@[{who}] DEALER ({d_score}):\n"
                    f"{d_inline}\n"
                    f"═════════════════\n"
                    f"YOU ({p_score}):\n{p_ascii}\n"
                    f"BUST! You lose.")
                    
        elif p_score == 21:
            # Auto-stand them if they hit exactly 21 to save them a radio transmission
            action = "stand"
        else:
            p_ascii = draw_ascii_hand(player_hand)
            d_inline = draw_inline_hand(dealer_hand, hide_second=True)
            return (f"@[{who}] DEALER:\n"
                    f"{d_inline}\n"
                    f"═════════════════\n"
                    f"YOU ({p_score}):\n{p_ascii}\n"
                    f"!blackjack h/s")

    # Handle 'Stand'
    if action in ["s", "stand"]:
        p_score = calculate_score(player_hand)
        d_score = calculate_score(dealer_hand)
        
        # Dealer must draw to 16 and stand on all 17s
        while d_score < 17:
            dealer_hand.append(deck.pop())
            d_score = calculate_score(dealer_hand)
            
        # Clear the game state
        del ACTIVE_GAMES[who]
        
        p_ascii = draw_ascii_hand(player_hand)
        d_inline = draw_inline_hand(dealer_hand, hide_second=False)
        
        msg = (f"@[{who}] DEALER ({d_score}):\n"
               f"{d_inline}\n"
               f"═════════════════\n"
               f"YOU ({p_score}):\n{p_ascii}\n")
        
        if d_score > 21:
            msg += "Dealer BUSTS! You WIN! 🎉"
        elif p_score > d_score:
            msg += "You WIN! 🎉"
        elif p_score < d_score:
            msg += "Dealer wins. 💸"
        else:
            msg += "PUSH! It's a tie. 🤝"
            
        return msg

    return f"@[{who}] Invalid action. Use '!blackjack h' or '!blackjack s'."