#pragma once

// Feed-neutral book event: what a market-data decoder hands the book layer.
// The type now lives in pkmn_book/book_event.hpp (shared with the Pokemon
// ladder builder); this alias keeps every md caller compiling unchanged.

#include "pkmn_book/book_event.hpp"

namespace pkmn::md {

using BookEvent = book::BookEvent;

}  // namespace pkmn::md
