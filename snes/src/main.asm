; =============================================================================
; SNES Terminal — interactive input line
;
; Reads SNES joypad combos injected by snes-terminal-bridge, looks up the
; corresponding ASCII tile, and writes it to BG1 row 0 left→right.
;
; Protocol (handled entirely in hardware/ROM):
;   - Combo must be stable (unchanged) for ≥ 2 consecutive VBlanks (debounce).
;   - Same combo is not re-triggered until all buttons are released.
;   - Cursor advances left→right, stops at column 15.
;
; VRAM layout:
;   $0000–$07FF  BG1 tilemap  (32×32 entries × 2 bytes = 2 KB)
;   $1000–$1BFF  Font tiles   (384 subtiles × 16 bytes = 6 KB)
;
; BG mode: Mode 0, BG1, 16×16 tiles, 2bpp, 4 colours
; Palette:  colour 0 = black ($0000), colour 1 = white ($7FFF)
; =============================================================================

.setcpu "65816"

; -----------------------------------------------------------------------------
; Hardware registers
; -----------------------------------------------------------------------------

INIDISP  = $2100
BGMODE   = $2105
BG1SC    = $2107
BG12NBA  = $210B
TM       = $212C
CGADD    = $2121
CGDATA   = $2122
VMAIN    = $2115
VMADDL   = $2116
VMADDH   = $2117
VMDATAL  = $2118
VMDATAH  = $2119
HVBJOY   = $4212   ; bit 7: VBlank active, bit 0: auto-joypad busy
NMITIMEN = $4200
JOY1L    = $4218   ; controller 1 low  byte: A, X, L, R, 0, 0, 0, 0  (bit7=A)
JOY1H    = $4219   ; controller 1 high byte: B, Y, Sel, Start, Up, Dn, Left, Right (bit7=B)
DMAP0    = $4300
BBAD0    = $4301
A1TL0    = $4302
A1TH0    = $4303
A1B0     = $4304
DAS0L    = $4305
DAS0H    = $4306
MDMAEN   = $420B

; Tile data constants (must match gen_font.py)
NUM_GROUPS  = 12
TOTAL_TILES = NUM_GROUPS * 32
FONT_BYTES  = TOTAL_TILES * 16   ; 6144 bytes

TILEMAP_BYTES = 1024 * 2         ; 2048 bytes

; -----------------------------------------------------------------------------
; Direct-page variables ($00–$0A, zeroed in init)
; -----------------------------------------------------------------------------

cursor_x        = $00   ; current column (0–15)
prev_joy_lo     = $01   ; JOY1L from previous frame
prev_joy_hi     = $02   ; JOY1H from previous frame
stable_cnt      = $03   ; consecutive frames with same joypad state
last_trig_lo    = $04   ; JOY1L of last triggered combo
last_trig_hi    = $05   ; JOY1H of last triggered combo
cur_joy_lo      = $06   ; JOY1L snapshot this frame
cur_joy_hi      = $07   ; JOY1H snapshot this frame
pending_tile_lo = $08   ; tile number low byte — written to VRAM next VBlank
pending_tile_hi = $09   ; tile number high byte
pending_flag    = $0A   ; $01 = tile write pending
boot_ready      = $0B   ; $01 after first clean frame (all buttons released)

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

reset:
    sei
    clc
    xce                         ; → native (65816) mode

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

    ; -------------------------------------------------------------------------
    ; Zero direct-page variables $00–$0A
    ; -------------------------------------------------------------------------
    ldx     #$000B
@zero_dp:
    stz     $00,x
    dex
    bpl     @zero_dp

    sep     #$10                ; X=8-bit
    .i8

    ; -------------------------------------------------------------------------
    ; Palette: colour 0 = black, colour 1 = white
    ; -------------------------------------------------------------------------
    stz     CGADD
    stz     CGDATA
    stz     CGDATA
    lda     #$FF
    sta     CGDATA
    lda     #$7F
    sta     CGDATA

    lda     #$80
    sta     VMAIN               ; VRAM increment after VMDATAH write

    ; =========================================================================
    ; DMA 1 — tilemap → VRAM $0000
    ; =========================================================================
    stz     VMADDL
    stz     VMADDH

    lda     #$01
    sta     DMAP0
    lda     #$18
    sta     BBAD0
    lda     #<tilemap_data
    sta     A1TL0
    lda     #>tilemap_data
    sta     A1TH0
    lda     #^tilemap_data
    sta     A1B0

    rep     #$20
    .a16
    lda     #TILEMAP_BYTES
    sta     DAS0L
    sep     #$20
    .a8

    lda     #$01
    sta     MDMAEN

    ; =========================================================================
    ; DMA 2 — font tiles → VRAM $1000
    ; =========================================================================
    stz     VMADDL
    lda     #$10
    sta     VMADDH

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

    ; =========================================================================
    ; BG configuration
    ; =========================================================================
    lda     #$10                ; Mode 0, BG1 16×16 tiles
    sta     BGMODE
    lda     #$00                ; BG1 tilemap at VRAM $0000
    sta     BG1SC
    lda     #$01                ; BG1 tile data at VRAM $1000
    sta     BG12NBA
    lda     #$01                ; enable BG1
    sta     TM

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
    ; Write pending tile to VRAM
    ; -------------------------------------------------------------------------
    lda     pending_flag
    beq     @no_pending
    stz     pending_flag

    ; Check for delete action (tile sentinel $FFFF)
    lda     pending_tile_hi
    cmp     #$FF
    beq     @do_delete

    ; Normal tile write — skip if line full (cursor_x = 16 = past last column)
    lda     cursor_x
    cmp     #16
    bcs     @no_pending

    sta     VMADDL
    stz     VMADDH
    lda     pending_tile_lo
    sta     VMDATAL
    lda     pending_tile_hi
    sta     VMDATAH

    ; Advance cursor; 16 means "past end / line full"
    inc     cursor_x
    bra     @no_pending

@do_delete:
    ; Move cursor back one column and erase tile with space (tile 0)
    lda     cursor_x
    beq     @no_pending
    dec     cursor_x
    lda     cursor_x
    sta     VMADDL
    stz     VMADDH
    stz     VMDATAL
    stz     VMDATAH

@no_pending:

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
    cmp     #$FF                ; cap at 255 to avoid wrap
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
    ; Ignore all input until we've seen at least one clean (buttons=0) frame.
    ; This prevents stuck keys from previous sessions showing up on boot.
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

    rep     #$10                ; X=16-bit for table indexing
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

    ; Match — queue tile for next VBlank write
    lda     keymap_data+2,x
    sta     pending_tile_lo
    lda     keymap_data+3,x
    sta     pending_tile_hi
    lda     #$01
    sta     pending_flag
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

; -----------------------------------------------------------------------------
; Data
; -----------------------------------------------------------------------------

.segment "RODATA"

tilemap_data:
.include "../assets/tilemap.inc"

font_tiles:
.include "../assets/font.inc"

keymap_data:
.include "../assets/keymap.inc"

; -----------------------------------------------------------------------------
; SNES internal header  ($FFC0–$FFE3)
; -----------------------------------------------------------------------------

.segment "HEADER"
    .byte "SNES TERMINAL       "
    .byte $20
    .byte $00
    .byte $05
    .byte $00
    .byte $01
    .byte $00
    .byte $00
    .word $FFFF
    .word $0000
    .byte $00, $00, $00, $00

; -----------------------------------------------------------------------------
; Interrupt vectors  ($FFE4–$FFFF)
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
