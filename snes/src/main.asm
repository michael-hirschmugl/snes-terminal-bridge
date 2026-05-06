; =============================================================================
; SNES Terminal — interactive multi-row input (Mode 5) + retro border frame
;
; Horizontal hi-res + interlace (512x448).
; BG2 2bpp 16x16 dense-packed tiles for text; BG1 4bpp 16x16 tiles for
; the border frame (Option D SNES-RPG style, 16 px margin, gen_border.py).
;
; Protocol:
;   - Combo must be stable (unchanged) for >= 2 consecutive VBlanks (debounce).
;   - Same combo is not re-triggered until all buttons are released.
;   - Cursor advances left->right, wraps to next row at column 32.
;   - KEY_ENTER ($FFFE) moves to a new row; viewport scrolls when needed.
;   - KEY_DELETE ($FFFF) erases the last character (sets tilemap entry = 0).
;
; VRAM layout (word addresses):
;   $0000-$0BFF  BG2 tile data   (384 × 8x8 2bpp tiles = 6144 bytes)
;                Character C (C = ord(ch) - 0x20) has top-left 8x8 slot
;                N(C) = (C // 8) * 32 + (C % 8) * 2; PPU auto-reads
;                N, N+1, N+16, N+17 per tilemap entry.
;   $1000-$13FF  BG2 tilemap     (32×32 entries × 2 bytes = 2048 bytes)
;                Zero-cleared at boot; tile index 0 = space (blank glyph).
;   $2000-$4FFF  BG1 tile data   (768 × 8x8 4bpp tiles = 24576 bytes, bank 1)
;   $5000-$53FF  BG1 tilemap     (32×32 entries × 2 bytes = 2048 bytes, bank 1)
;
; CGRAM layout:
;   $00-$0F  BG1 sub-palette 0 (16 colours, border frame)
;   $1C-$1F  BG2 sub-palette 7 (4 colours, text AA: transparent/dark/light/white)
;
; BG mode:    Mode 5 + interlace (hires 512×448), BG1+BG2, 16×16 tiles
; Characters: 16 × 16 px anti-aliased (JetBrains Mono via gen_font.py)
;             Tilemap entry: priority=1, palette=7 → text renders above wallpaper
; Grid:       30 columns × 26 visible rows (cols 1-30, 16px margin all sides)
; =============================================================================

.setcpu "65816"
.smart on

; -----------------------------------------------------------------------------
; Hardware registers
; -----------------------------------------------------------------------------

INIDISP  = $2100
BGMODE   = $2105
BG1SC    = $2107   ; BG1 tilemap base + size
BG2SC    = $2108
BG12NBA  = $210B
BG1HOFS  = $210D   ; BG1 horizontal scroll (write twice: low then high byte)
BG1VOFS  = $210E   ; BG1 vertical scroll   (write twice: low then high byte)
BG2HOFS  = $210F   ; BG2 horizontal scroll (write twice: low then high byte)
BG2VOFS  = $2110   ; BG2 vertical scroll   (write twice: low then high byte)
VMAIN    = $2115
VMADDL   = $2116
VMADDH   = $2117
VMDATAL  = $2118
VMDATAH  = $2119
CGADD    = $2121
CGDATA   = $2122
TM       = $212C   ; main screen enable (odd hires pixels in Mode 5)
TS       = $212D   ; sub  screen enable (even hires pixels in Mode 5)
TMW      = $212E   ; window masking for main screen (must be 0 — see init)
TSW      = $212F   ; window masking for sub  screen (must be 0 — see init)
CGWSEL   = $2130   ; color math window select       (must be 0 — see init)
CGADSUB  = $2131   ; color math designation         (must be 0 — see init)
SETINI   = $2133   ; display settings: bit 0 = interlace enable
WMDATA   = $2180
WMADDL   = $2181
WMADDM   = $2182
WMADDH   = $2183
APUIO0   = $2140
NMITIMEN = $4200
WRIO     = $4201
HVBJOY   = $4212   ; bit 7: VBlank active, bit 0: auto-joypad busy
JOY1L    = $4218   ; controller 1 low  byte: A, X, L, R, 0, 0, 0, 0
JOY1H    = $4219   ; controller 1 high byte: B, Y, Sel, Start, Up, Dn, Left, Right
DMAP0    = $4300
BBAD0    = $4301
A1TL0    = $4302
A1TH0    = $4303
A1B0     = $4304
DAS0L    = $4305
DAS0H    = $4306
MDMAEN   = $420B
HDMAEN   = $420C
MEMSEL   = $420D

; -----------------------------------------------------------------------------
; Mode-5 layout constants (must match gen_font.py / gen_keymap.py)
; -----------------------------------------------------------------------------

TILE_BYTES      = 16               ; 2bpp 8x8 tile = 16 bytes
NUM_GLYPHS      = 95               ; ASCII 0x20..0x7E
TOTAL_TILES     = 384              ; N(BLANK) + 18 = 366 + 18; see gen_font.py
FONT_BYTES      = TOTAL_TILES * TILE_BYTES    ; 6144 bytes of tile data

TILEMAP_WORD    = $1000            ; VRAM word address of tilemap base
TILEMAP_BYTES   = 32 * 32 * 2      ; 2048 bytes

VISIBLE_ROWS    = 26               ; rows kept on screen before scrolling
TILEMAP_ROWS    = 32               ; circular buffer size
ROW_PIXEL_H     = 16               ; 16x16 tile -> 16 pixel row height

LEFT_COL        = 1                ; first writable column (16px left margin)
RIGHT_COL       = 30               ; last  writable column (16px right margin)
USABLE_COLS     = 30               ; RIGHT_COL - LEFT_COL + 1
PROMPT_COL      = LEFT_COL + 1    ; cursor lands here after the ">" prompt
INPUT_BUF_MAX   = 29              ; RIGHT_COL - PROMPT_COL + 1 (chars per input line)

CURSOR_TILE_LO  = $EE              ; '_' ASCII 95, tile N(63)=238
CURSOR_TILE_HI  = $3C              ; priority=1, palette=7

; -----------------------------------------------------------------------------
; Direct-page variables ($00-$11, zeroed in init)
; -----------------------------------------------------------------------------

cursor_x        = $00   ; current column (LEFT_COL..RIGHT_COL = 1..30)
prev_joy_lo     = $01   ; JOY1L from previous frame
prev_joy_hi     = $02   ; JOY1H from previous frame
stable_cnt      = $03   ; consecutive frames with same joypad state
last_trig_lo    = $04   ; JOY1L of last triggered combo
last_trig_hi    = $05   ; JOY1H of last triggered combo
cur_joy_lo      = $06   ; JOY1L snapshot this frame
cur_joy_hi      = $07   ; JOY1H snapshot this frame
pending_tile_lo = $08   ; tilemap entry low byte — written next VBlank
pending_tile_hi = $09   ; tilemap entry high byte
pending_flag    = $0A   ; $01 = tilemap write pending
boot_ready      = $0B   ; $01 after first clean frame (all buttons released)
cursor_y        = $0C   ; current character row (0-31, circular)
top_vram_row    = $0D   ; topmost visible character row (0-31)
addr_scratch    = $0E   ; 16-bit VRAM word address scratch ($0E=low, $0F=high)
blink_ctr       = $10   ; cursor blink counter; bit 5 drives ~1Hz blink
auto_wrap       = $11   ; $01 = newline was triggered by line-wrap, not Enter
buf_len         = $12   ; chars in current input line (0–INPUT_BUF_MAX); zeroed by WRAM DMA at boot
input_buf       = $0020 ; 29-byte WRAM ASCII buffer (indices 0..buf_len-1); zeroed by WRAM DMA at boot
line_ready      = $003D ; $01 when Enter pressed (line complete); host clears to $00 after reading

; -----------------------------------------------------------------------------
; CODE
; -----------------------------------------------------------------------------

.segment "CODE"

nmi_handler:
irq_handler:
cop_handler:
brk_handler:
abort_handler:
    rti

; =============================================================================
; calc_tilemap_addr — compute VRAM word address of the tilemap entry for the
;                     character at (cursor_x, cursor_y) and store it in
;                     addr_scratch.
;
;   addr = TILEMAP_WORD + (cursor_y & $1F) * 32 + (cursor_x & $1F)
;
; Call with 8-bit A; routine switches to 16-bit A internally and restores 8-bit.
; X register width is not changed.
; =============================================================================

calc_tilemap_addr:
    rep  #$20
    .a16

    lda  cursor_y
    and  #$001F
    asl                  ; * 2
    asl                  ; * 4
    asl                  ; * 8
    asl                  ; * 16
    asl                  ; * 32
    clc
    adc  #TILEMAP_WORD
    sta  addr_scratch

    lda  cursor_x
    and  #$001F
    clc
    adc  addr_scratch
    sta  addr_scratch

    sep  #$20
    .a8
    rts

; =============================================================================
; reset
; =============================================================================

reset:
    sei
    clc
    xce                         ; -> native (65816) mode

    rep     #$30                ; A=16-bit, X=16-bit
    .a16
    .i16
    lda     #$1FFF
    tcs                         ; stack = $1FFF
    lda     #$0000
    tcd                         ; direct page = $0000

    sep     #$20                ; A=8-bit
    .a8

    lda     #$8F                ; force blank
    sta     INIDISP
    stz     NMITIMEN
    stz     HDMAEN
    stz     MDMAEN
    stz     WRIO
    stz     APUIO0
    stz     APUIO0+1
    stz     APUIO0+2
    stz     APUIO0+3
    stz     MEMSEL              ; SlowROM timing

    ; -------------------------------------------------------------------------
    ; Zero direct-page variables $00-$0F
    ; -------------------------------------------------------------------------
    ldx     #$0010
    .i16
@zero_dp:
    stz     $00,x
    dex
    bpl     @zero_dp

    sep     #$10                ; X=8-bit
    .i8

    ; =========================================================================
    ; Clear WRAM (128 KiB) via fixed-destination DMA to $2180
    ; =========================================================================
    stz     WMADDL
    stz     WMADDM
    stz     WMADDH

    lda     #$08                ; CPU->PPU, fixed source
    sta     DMAP0
    lda     #$80                ; WMDATA low byte
    sta     BBAD0
    lda     #<ZeroByte
    sta     A1TL0
    lda     #>ZeroByte
    sta     A1TH0
    lda     #^ZeroByte
    sta     A1B0
    stz     DAS0L               ; size = 0 => 65536 bytes
    stz     DAS0H
    lda     #$01
    sta     MDMAEN
    lda     #$01
    sta     MDMAEN              ; second 64 KiB

    ; =========================================================================
    ; Clear VRAM (64 KiB) by writing zeros through VMDATAL/H
    ; =========================================================================
    stz     VMADDL
    stz     VMADDH
    lda     #$80                ; VRAM increment after high-byte write, +1 word
    sta     VMAIN

    lda     #$09                ; CPU->PPU, 2-reg write once (2118/2119), fixed src
    sta     DMAP0
    lda     #$18                ; VMDATAL
    sta     BBAD0
    lda     #<ZeroWord
    sta     A1TL0
    lda     #>ZeroWord
    sta     A1TH0
    lda     #^ZeroWord
    sta     A1B0
    stz     DAS0L               ; size = 0 => 65536 bytes
    stz     DAS0H
    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; Clear CGRAM (512 bytes)
    ; =========================================================================
    stz     CGADD
    lda     #$08                ; CPU->PPU, fixed source
    sta     DMAP0
    lda     #$22                ; CGDATA
    sta     BBAD0
    lda     #<ZeroByte
    sta     A1TL0
    lda     #>ZeroByte
    sta     A1TH0
    lda     #^ZeroByte
    sta     A1B0
    stz     DAS0L
    lda     #$02                ; 512 bytes
    sta     DAS0H
    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 1a — BG1 palette (16 colours, 32 bytes) -> CGRAM $00..$0F
    ; Sub-palette 0 for BG1 4bpp border frame.
    ; =========================================================================
    stz     CGADD
    lda     #$00                ; CPU->PPU, 1-reg, auto-increment
    sta     DMAP0
    lda     #$22                ; CGDATA
    sta     BBAD0
    lda     #<bg1_palette_data
    sta     A1TL0
    lda     #>bg1_palette_data
    sta     A1TH0
    lda     #^bg1_palette_data
    sta     A1B0

    rep     #$20
    .a16
    lda     #32
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 1b — BG2 text palette (4 colours, 8 bytes) -> CGRAM $1C..$1F
    ; Sub-palette 7 for BG2 2bpp text layer (priority=1 tiles use palette 7).
    ; Colour 0 = transparent, 1 = dark grey, 2 = light grey, 3 = white.
    ; =========================================================================
    lda     #$1C                ; CGRAM address = sub-palette 7 start
    sta     CGADD
    lda     #$00                ; CPU->PPU, 1-reg, auto-increment
    sta     DMAP0
    lda     #$22                ; CGDATA
    sta     BBAD0
    lda     #<palette_data
    sta     A1TL0
    lda     #>palette_data
    sta     A1TH0
    lda     #^palette_data
    sta     A1B0

    rep     #$20
    .a16
    lda     #8
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 2 — BG2 tile data (dense-pack font) -> VRAM word $0000
    ; =========================================================================
    stz     VMADDL
    stz     VMADDH
    lda     #$80                ; VRAM increment after high byte, +1 word
    sta     VMAIN

    lda     #$01                ; CPU->PPU, 2-reg write once (2118/2119), auto-inc
    sta     DMAP0
    lda     #$18                ; VMDATAL
    sta     BBAD0
    lda     #<font_tiles
    sta     A1TL0
    lda     #>font_tiles
    sta     A1TH0
    lda     #^font_tiles
    sta     A1B0

    rep     #$20
    .a16
    lda     #FONT_BYTES
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; The BG2 tilemap at VRAM word $1000 was zero-cleared by the VRAM-wipe.
    ; Tile index 0 = space glyph (all-zero sub-tiles), so every empty cell
    ; renders as transparent, showing the BG1 wallpaper through.

    ; =========================================================================
    ; DMA 3 — BG1 tile data (4bpp, 1024 bytes, bank 1) -> VRAM word $2000
    ; gen_border.py produces tiles.4bpp.chr with 32 8x8 tile slots (4 unique
    ; 16x16 super-tiles: corner, h-edge, v-edge, blank; dense-packed).
    ; =========================================================================
    lda     #$00
    sta     VMADDL
    lda     #$20                ; VRAM word $2000
    sta     VMADDH
    lda     #$80                ; increment after high-byte write, +1 word
    sta     VMAIN

    lda     #$01                ; CPU->PPU, destination increments
    sta     DMAP0
    lda     #$18                ; VMDATAL
    sta     BBAD0
    lda     #<bg1_tile_data
    sta     A1TL0
    lda     #>bg1_tile_data
    sta     A1TH0
    lda     #^bg1_tile_data     ; bank $01 (RODATA1 segment)
    sta     A1B0

    rep     #$20
    .a16
    lda     #$0400              ; 1024 bytes (32 slots × 32 bytes)
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 4 — BG1 tilemap (2048 bytes, bank 1) -> VRAM word $5000
    ; 32x32 entries; 32x28 visible rows fill 512x448; bottom 4 rows are blank.
    ; =========================================================================
    lda     #$00
    sta     VMADDL
    lda     #$50                ; VRAM word $5000
    sta     VMADDH
    lda     #$80
    sta     VMAIN

    lda     #$01
    sta     DMAP0
    lda     #$18
    sta     BBAD0
    lda     #<bg1_tilemap_data
    sta     A1TL0
    lda     #>bg1_tilemap_data
    sta     A1TH0
    lda     #^bg1_tilemap_data  ; bank $01
    sta     A1B0

    rep     #$20
    .a16
    lda     #$0800              ; 2048 bytes
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; BG configuration (Mode 5 + interlace, BG1 4bpp + BG2 2bpp, 16x16)
    ; =========================================================================
    lda     #$35                ; Mode 5 + BG1 16x16 (bit 4) + BG2 16x16 (bit 5)
    sta     BGMODE
    lda     #$50                ; BG1 tilemap @ word $5000, 32x32
    sta     BG1SC
    lda     #$10                ; BG2 tilemap @ word $1000, 32x32
    sta     BG2SC
    lda     #$02                ; BG1 char base @ word $2000, BG2 char base @ $0000
    sta     BG12NBA

    stz     BG1HOFS
    stz     BG1HOFS
    stz     BG1VOFS
    stz     BG1VOFS
    stz     BG2HOFS
    stz     BG2HOFS
    lda     #$F0                ; BG2VOFS = $01F0 = -16 (9-bit): tilemap row 31
    sta     BG2VOFS             ;   at screen Y=0 -> permanent blank top margin
    lda     #$01
    sta     BG2VOFS

    lda     #$01                ; interlace enable -> 448 lines
    sta     SETINI

    lda     #$03                ; BG1+BG2 on main screen (odd hires columns)
    sta     TM
    lda     #$03                ; BG1+BG2 on sub  screen (even hires columns)
    sta     TS

    ; Real hardware leaves these undefined; emulators default to 0.
    ; TMW/TSW: if bit 1 is set, BG2 gets window-masked -> black screen.
    ; CGADSUB: bit 7=1 (subtract) + bit 1=1 (BG2) means BG2_main - BG2_sub = 0
    ;   because TM and TS both show BG2, so every pixel cancels to black.
    stz     TMW                 ; no window masking on main screen
    stz     TSW                 ; no window masking on sub  screen
    stz     CGWSEL              ; no color-math windowing
    stz     CGADSUB             ; no color math (addition / subtraction)
    stz     $2123               ; W12SEL: no BG1/BG2 window enables
    stz     $2124               ; W34SEL: no BG3/BG4 window enables

    lda     #LEFT_COL           ; WRAM DMA cleared cursor_x to 0; restore now
    sta     cursor_x

    jsr     print_welcome_msg   ; write welcome message to VRAM (screen still blanked)
    jsr     print_prompt        ; print "> " and position cursor at PROMPT_COL

    lda     #$01                ; enable auto-joypad read
    sta     NMITIMEN

    lda     #$0F                ; display on, full brightness
    sta     INIDISP

; =============================================================================
; Main loop
; =============================================================================

@main_loop:

    ; -------------------------------------------------------------------------
    ; Wait for VBlank start (safest time to write VRAM)
    ; -------------------------------------------------------------------------
@wait_vblank:
    lda     HVBJOY
    and     #$80
    beq     @wait_vblank

    ; -------------------------------------------------------------------------
    ; Cursor erase — overwrite cursor cell with blank before pending tile
    ; changes cursor_x/cursor_y, so the old position is always cleaned up.
    ; Skip when cursor_x > RIGHT_COL (buffer full, cursor parked off-screen).
    ; -------------------------------------------------------------------------
    lda     cursor_x
    cmp     #RIGHT_COL + 1
    bcs     @skip_erase
    jsr     calc_tilemap_addr
    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    stz     VMDATAL
    stz     VMDATAH
@skip_erase:

    ; -------------------------------------------------------------------------
    ; Write pending tilemap entry to VRAM
    ; -------------------------------------------------------------------------
    lda     pending_flag
    bne     :+
    jmp     @no_pending
:
    stz     pending_flag

    ; Check for special action (pending_tile_hi = $FF)
    lda     pending_tile_hi
    cmp     #$FF
    bne     @normal_tile

    ; Special: distinguish by low byte
    lda     pending_tile_lo
    cmp     #$FF
    beq     @do_delete
    cmp     #$FE
    bne     :+
    jmp     @do_newline
:
    jmp     @no_pending          ; unknown sentinel

    ; -----------------------------------------------------------------------
    ; Normal character — a single tilemap word at (cursor_x, cursor_y).
    ; The PPU auto-reads the four dense-pack sub-tiles (N, N+1, N+16, N+17)
    ; to assemble the full 16x16 glyph.
    ; -----------------------------------------------------------------------
@normal_tile:
    lda     cursor_x
    cmp     #RIGHT_COL + 1
    bcc     :+
    jmp     @no_pending          ; line full, newline still pending
:
    jsr     calc_tilemap_addr    ; -> addr_scratch = VRAM word addr of cell

    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL               ; 16-bit store sets VMADDL + VMADDH
    sep     #$20
    .a8

    lda     pending_tile_lo      ; tile index N(C) low byte
    sta     VMDATAL
    lda     pending_tile_hi      ; flip/palette/priority bits (= 0 here)
    sta     VMDATAH

    ; advance cursor; > RIGHT_COL -> buffer full, clamp (no auto-wrap)
    inc     cursor_x
    lda     cursor_x
    cmp     #RIGHT_COL + 1
    bcs     :+
    jmp     @no_pending
:
    lda     buf_len
    cmp     #INPUT_BUF_MAX
    bcc     @no_clamp
    jmp     @no_pending         ; buffer full: cursor stays at 31 (off-screen), erase/blink skipped below
@no_clamp:
    lda     #$FE
    sta     pending_tile_lo
    lda     #$FF
    sta     pending_tile_hi
    lda     #$01
    sta     pending_flag
    sta     auto_wrap           ; mark as auto-wrap (not Enter)
    jmp     @no_pending

    ; -----------------------------------------------------------------------
    ; KEY_DELETE — erase last character: set tilemap entry to 0 (space).
    ; -----------------------------------------------------------------------
@do_delete:
    lda     cursor_x
    cmp     #PROMPT_COL
    bne     :+
    jmp     @no_pending
:
    dec     cursor_x
    dec     buf_len
    ldx     buf_len
    stz     input_buf,x         ; zero the freed ASCII slot
    jsr     calc_tilemap_addr

    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    stz     VMDATAL              ; entry = 0 -> space glyph
    stz     VMDATAH
    jmp     @no_pending

    ; -----------------------------------------------------------------------
    ; KEY_ENTER — advance to the next character row, scroll viewport if
    ;             needed (BG2VOFS), clear the new row (32 tilemap words).
    ; -----------------------------------------------------------------------
@do_newline:
    ldx     buf_len
    stz     input_buf,x         ; NUL-terminate at buf_len position
    lda     #$01
    sta     line_ready          ; signal line ready for host
    stz     buf_len             ; reset input buffer for new line
    ; cursor_y = (cursor_y + 1) & $1F
    lda     cursor_y
    inc     a
    and     #$1F
    sta     cursor_y

    ; visible_offset = (cursor_y - top_vram_row) & $1F
    ; if >= VISIBLE_ROWS: scroll viewport
    sec
    sbc     top_vram_row
    and     #$1F
    cmp     #VISIBLE_ROWS
    bcc     @newline_no_scroll

    ; top_vram_row = (cursor_y - (VISIBLE_ROWS - 1)) & $1F
    lda     cursor_y
    sec
    sbc     #(VISIBLE_ROWS - 1)
    and     #$1F
    sta     top_vram_row

    ; BG2VOFS = (top_vram_row * 16 - 16) & $1FF
    ; -16 shifts display down 1 row: row 31 (blank) appears at screen top.
    ; When top_vram_row=0: $0000 - $0010 = $FFF0 & $01FF = $01F0 -> row 31 ✓
    rep     #$20
    .a16
    lda     top_vram_row
    and     #$00FF
    asl
    asl
    asl
    asl                          ; * 16
    sec
    sbc     #$0010               ; - 16
    and     #$01FF               ; mask to 9 bits
    sep     #$20
    .a8
    sta     BG2VOFS              ; low byte
    xba
    sta     BG2VOFS              ; high byte

    ; Clear old top_vram_row (= new blank margin row now visible at screen top).
    ; old_top_vram_row = (cursor_y - VISIBLE_ROWS) & $1F
    rep     #$20
    .a16
    lda     cursor_y
    and     #$001F
    sec
    sbc     #VISIBLE_ROWS
    and     #$001F               ; old_top_vram_row
    asl
    asl
    asl
    asl
    asl                          ; * 32
    clc
    adc     #TILEMAP_WORD
    sta     VMADDL
    sep     #$20
    .a8

    ldx     #32
@cl_margin_row:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_margin_row

@newline_no_scroll:

    ; --- Clear new tilemap row: 32 sequential word writes --------------------
    ; addr = TILEMAP_WORD + (cursor_y & $1F) * 32
    rep     #$20
    .a16
    lda     cursor_y
    and     #$001F
    asl
    asl
    asl
    asl
    asl                          ; * 32
    clc
    adc     #TILEMAP_WORD
    sta     VMADDL               ; 16-bit store sets VMADDL + VMADDH
    sep     #$20
    .a8

    ldx     #32
@cl_row:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_row

    lda     auto_wrap
    stz     auto_wrap
    beq     :+                  ; not auto-wrap → print prompt
    lda     #LEFT_COL           ; auto-wrap: reset cursor without prompt
    sta     cursor_x
    bra     @no_pending
:   jsr     print_prompt        ; Enter only: print ">" and set cursor_x = PROMPT_COL
    bra     @no_pending

@no_pending:

    ; -------------------------------------------------------------------------
    ; Cursor blink draw — skipped when cursor_x > RIGHT_COL (buffer full)
    ; -------------------------------------------------------------------------
    inc     blink_ctr
    lda     cursor_x
    cmp     #RIGHT_COL + 1
    bcs     @cursor_done        ; parked off-screen: no visible cursor
    lda     blink_ctr
    and     #$20                ; bit 5: 0 = on, $20 = off
    bne     @cursor_done        ; off → cursor already erased above
    jsr     calc_tilemap_addr
    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    lda     #CURSOR_TILE_LO
    sta     VMDATAL
    lda     #CURSOR_TILE_HI
    sta     VMDATAH
@cursor_done:

    ; -------------------------------------------------------------------------
    ; Wait for VBlank to end before reading joypad
    ; -------------------------------------------------------------------------
@wait_active:
    lda     HVBJOY
    and     #$80
    bne     @wait_active

    ; -------------------------------------------------------------------------
    ; Wait for auto-joypad read to finish
    ; -------------------------------------------------------------------------
@wait_joy:
    lda     HVBJOY
    and     #$01
    bne     @wait_joy

    ; -------------------------------------------------------------------------
    ; Snapshot joypad registers
    ; -------------------------------------------------------------------------
    lda     JOY1H
    sta     cur_joy_hi
    lda     JOY1L
    sta     cur_joy_lo

    ; -------------------------------------------------------------------------
    ; Debounce: compare with previous frame's state
    ; -------------------------------------------------------------------------
    lda     cur_joy_lo
    cmp     prev_joy_lo
    bne     @state_changed
    lda     cur_joy_hi
    cmp     prev_joy_hi
    bne     @state_changed

    lda     stable_cnt
    cmp     #$FF                 ; cap at 255 to avoid wrap
    beq     @save_prev
    inc     stable_cnt
    bra     @save_prev

@state_changed:
    stz     stable_cnt

@save_prev:
    lda     cur_joy_lo
    sta     prev_joy_lo
    lda     cur_joy_hi
    sta     prev_joy_hi

    ; -------------------------------------------------------------------------
    ; Require stable for >= 2 frames
    ; -------------------------------------------------------------------------
    lda     stable_cnt
    cmp     #2
    bcs     @stable_ok
    jmp     @main_loop
@stable_ok:

    ; -------------------------------------------------------------------------
    ; Buttons = 0: mark boot_ready and clear last_trig
    ; -------------------------------------------------------------------------
    lda     cur_joy_lo
    ora     cur_joy_hi
    bne     @check_boot
    lda     #$01
    sta     boot_ready
    stz     last_trig_lo
    stz     last_trig_hi
    jmp     @main_loop

    ; -------------------------------------------------------------------------
    ; Ignore all input until we have seen one clean (buttons=0) frame so
    ; stuck keys from previous sessions do not show up on boot.
    ; -------------------------------------------------------------------------
@check_boot:
    lda     boot_ready
    bne     @boot_ok
    jmp     @main_loop
@boot_ok:

    ; -------------------------------------------------------------------------
    ; Same combo as last trigger: skip (no repeat while held)
    ; -------------------------------------------------------------------------
@check_repeat:
    lda     cur_joy_lo
    cmp     last_trig_lo
    bne     @do_lookup
    lda     cur_joy_hi
    cmp     last_trig_hi
    bne     @do_lookup
    jmp     @main_loop

    ; -------------------------------------------------------------------------
    ; New combo — record and search keymap
    ; -------------------------------------------------------------------------
@do_lookup:
    lda     cur_joy_lo
    sta     last_trig_lo
    lda     cur_joy_hi
    sta     last_trig_hi

    rep     #$10                 ; X=16-bit for table indexing
    .i16
    ldx     #0

@scan_loop:
    ; Sentinel: bitmask = $0000
    lda     keymap_data,x
    ora     keymap_data+1,x
    beq     @not_found

    ; Compare bitmask low byte with cur_joy_lo (JOY1L snapshot)
    lda     keymap_data,x
    cmp     cur_joy_lo
    bne     @next_entry

    ; Compare bitmask high byte with cur_joy_hi (JOY1H snapshot)
    lda     keymap_data+1,x
    cmp     cur_joy_hi
    bne     @next_entry

    ; Match — queue tilemap entry for next VBlank write
    lda     keymap_data+2,x
    sta     pending_tile_lo
    lda     keymap_data+3,x
    sta     pending_tile_hi
    ; Special keys (Enter=$FE/$FF, Delete=$FF/$FF) bypass buffer limit check
    cmp     #$FF
    beq     @set_pending
    ; Normal char: block if buffer full
    lda     buf_len
    cmp     #INPUT_BUF_MAX
    bcs     @scan_done          ; buf_len >= MAX → discard
    inc     buf_len
    jsr     tile_to_ascii       ; A = ASCII byte (clobbers addr_scratch)
    ldx     buf_len             ; X (16-bit) = buf_len after increment
    dex                         ; X = zero-based slot index
    sta     input_buf,x
@set_pending:
    lda     #$01
    sta     pending_flag
@scan_done:
    sep     #$10
    .i8
    jmp     @main_loop

@next_entry:
    inx
    inx
    inx
    inx
    jmp     @scan_loop

@not_found:
    sep     #$10
    .i8
    jmp     @main_loop

; =============================================================================
; tile_to_ascii — decode pending_tile_lo ($08) / pending_tile_hi ($09) to ASCII.
;
; Formula (inverse of gen_keymap.py dense-pack):
;   C_bits_5_3 = (tile_lo & $E0) >> 2
;   C_bits_2_0 = (tile_lo >> 1) & $07
;   C_bit_6    = pending_tile_hi & $01   (1 when tile >= 256, i.e. lowercase etc.)
;   ascii = C_bits_5_3 | C_bits_2_0 | (C_bit_6 << 6) + $20
;
; Output: A = ASCII byte ($20–$7E). Clobbers addr_scratch ($0E). Preserves X, Y.
; =============================================================================
tile_to_ascii:
    lda     pending_tile_lo
    and     #$E0
    lsr
    lsr
    sta     addr_scratch
    lda     pending_tile_lo
    lsr
    and     #$07
    ora     addr_scratch
    pha
    lda     pending_tile_hi
    and     #$01
    beq     @no_hi
    pla
    ora     #$40
    bra     @done
@no_hi:
    pla
@done:
    clc
    adc     #$20
    rts

; =============================================================================
; print_welcome_msg — write welcome_data to VRAM during init (screen blanked).
;
; Reads .word entries from welcome_data: printable chars as tile words
; (0x3C00|N), $FFFF = newline, $0000 = end sentinel.
; Reuses calc_tilemap_addr + pending_tile_lo/hi/addr_scratch.
; Entry: A=8-bit, X=8-bit, cursor_x=LEFT_COL, cursor_y=0, VMAIN=$80.
; Exit:  A=8-bit, X=8-bit; cursor at row after last message line.
; =============================================================================

print_welcome_msg:
    rep     #$10
    .i16
    ldx     #$0000
@pw_loop:
    rep     #$20
    .a16
    lda     welcome_data,x
    sep     #$20
    .a8
    sta     pending_tile_lo
    xba
    sta     pending_tile_hi
    lda     pending_tile_lo
    ora     pending_tile_hi
    beq     @pw_done            ; $0000 = sentinel
    lda     pending_tile_lo
    and     pending_tile_hi
    cmp     #$FF
    beq     @pw_nl              ; $FFFF = newline
    inx
    inx
    jsr     calc_tilemap_addr
    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL              ; 16-bit store sets VMADDL+VMADDH
    sep     #$20
    .a8
    lda     pending_tile_lo
    sta     VMDATAL
    lda     pending_tile_hi
    sta     VMDATAH
    inc     cursor_x
    bra     @pw_loop
@pw_nl:
    inx
    inx
    lda     cursor_y
    inc     a
    and     #$1F
    sta     cursor_y
    lda     #LEFT_COL
    sta     cursor_x
    bra     @pw_loop
@pw_done:
    sep     #$10
    .i8
    rts

; =============================================================================
; print_prompt — write '>' tile at (cursor_y, LEFT_COL), set cursor_x = PROMPT_COL.
;
; Must be called during VBlank or while screen is blanked (direct VRAM write).
; Entry: A=8-bit, cursor_y = current row, VMAIN=$80.
; Exit:  A=8-bit, cursor_x = PROMPT_COL.
; =============================================================================

print_prompt:
    lda     #LEFT_COL
    sta     cursor_x
    jsr     calc_tilemap_addr       ; addr_scratch = VRAM word addr of (cursor_y, LEFT_COL)
    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    lda     #$6C                    ; '>' tile index low byte  (index 30 from space: (30//8)*32+(30%8)*2 = $6C)
    sta     VMDATAL
    lda     #$3C                    ; priority=1, palette=7
    sta     VMDATAH
    lda     #PROMPT_COL
    sta     cursor_x
    rts

; -----------------------------------------------------------------------------
; Data
; -----------------------------------------------------------------------------

.segment "RODATA"

ZeroByte:
    .byte $00
ZeroWord:
    .word $0000

; BG1 border palette: 16 colours (32 bytes) for sub-palette 0 (CGRAM $00-$0F).
; Generated by tools/gen_border.py (blue frame + gold diamond corner ornament).
bg1_palette_data:
    .incbin "../build/mode5_border_4bpp/palette.bin"

; BG2 text palette: 4 colours (8 bytes) for sub-palette 7 (CGRAM $1C-$1F).
; Colour 0 = transparent, 1/2 = AA mid-greys, 3 = white.
palette_data:
    .word $0000      ; 0  transparent (BG1 shows through)
    .word $294A      ; 1  dark grey  (r=10,g=10,b=10 in BGR555)
    .word $56B5      ; 2  light grey (r=21,g=21,b=21)
    .word $7FFF      ; 3  white

font_tiles:
.include "../assets/font.inc"

keymap_data:
.include "../assets/keymap.inc"

welcome_data:
.include "../assets/welcome.inc"

; -----------------------------------------------------------------------------
; SNES internal header  ($FFC0-$FFE3)
; -----------------------------------------------------------------------------

.segment "HEADER"
    .byte "SNES TERMINAL+BORDER "
    .byte $20                    ; map mode: LoROM, SlowROM
    .byte $00                    ; cartridge type: ROM only
    .byte $08                    ; ROM size: $08 required for Everdrive LoROM mapping
                                 ; ($05 = 32 KiB per SNES spec, but Everdrive maps
                                 ; it as "8m" and places ROM at wrong address; $08
                                 ; makes Everdrive select "512k" mapping, which
                                 ; correctly mirrors this ROM)
    .byte $00                    ; RAM size: 0
    .byte $02                    ; destination code: Europe (PAL)
    .byte $00                    ; old licensee code (Nintendo)
    .byte $00                    ; version
    .word $FFFF                  ; checksum complement (patched by fix_checksum.py)
    .word $0000                  ; checksum (patched by fix_checksum.py)

; -----------------------------------------------------------------------------
; Interrupt vectors  ($FFE4-$FFFF)
; -----------------------------------------------------------------------------

.segment "VECTORS"
    .word cop_handler
    .word brk_handler
    .word abort_handler
    .word nmi_handler
    .word $0000
    .word irq_handler
    .word $0000
    .word $0000
    .word cop_handler
    .word $0000
    .word abort_handler
    .word nmi_handler
    .word reset
    .word irq_handler

; -----------------------------------------------------------------------------
; BG1 border data — bank 1 ($01:8000+), RODATA1 segment
; Loaded by DMA 3 (tiles -> VRAM $2000) and DMA 4 (tilemap -> VRAM $5000).
; -----------------------------------------------------------------------------

.segment "RODATA1"

bg1_tile_data:
    .incbin "../build/mode5_border_4bpp/tiles.4bpp.chr"

bg1_tilemap_data:
    .incbin "../build/mode5_border_4bpp/tilemap.bin"
