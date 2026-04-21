; =============================================================================
; SNES Terminal — interactive multi-row input, Mode 1
;
; Reads SNES joypad combos injected by snes-terminal-bridge, looks up the
; corresponding ASCII tile, and writes it to BG1 in a scrolling 32×14 grid.
;
; Protocol (handled entirely in hardware/ROM):
;   - Combo must be stable (unchanged) for ≥ 2 consecutive VBlanks (debounce).
;   - Same combo is not re-triggered until all buttons are released.
;   - Cursor advances left→right, wraps to next row at column 32.
;   - KEY_ENTER ($FFFE) moves to a new row; viewport scrolls when needed.
;   - KEY_DELETE ($FFFF) erases the last character.
;
; VRAM layout:
;   $0000–$1FFF  BG1 tilemap  (64×64 entries × 2 bytes = 8 KB)
;   $2000–$37BF  Font tiles   (190 tiles × 32 bytes = 6080 bytes)
;
; BG mode:    Mode 1 (256×224), BG1 only, 8×8 tiles, 4bpp
; Characters: 8px wide × 16px tall (2 stacked 8×8 tiles: top + bottom)
; Grid:       32 columns × 14 visible rows (32-row circular buffer)
; Palette:    colour 0 = black ($0000), colour 1 = white ($7FFF)
; =============================================================================

.setcpu "65816"

; -----------------------------------------------------------------------------
; Hardware registers
; -----------------------------------------------------------------------------

INIDISP  = $2100
BGMODE   = $2105
BG1SC    = $2107
BG2SC    = $2108
BG12NBA  = $210B
BG1HOFS  = $210D   ; BG1 horizontal scroll (write twice: low then high byte)
BG1VOFS  = $210E   ; BG1 vertical scroll  (write twice: low then high byte)
BG2HOFS  = $210F   ; BG2 horizontal scroll (write twice: low then high byte)
BG2VOFS  = $2110   ; BG2 vertical scroll  (write twice: low then high byte)
TM       = $212C   ; main screen enable (even hires pixels in Mode 5)
TMW      = $212D   ; sub screen enable  (odd  hires pixels in Mode 5)
SETINI   = $2133   ; display settings: bit 3 = hi-res mode enable
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
TOTAL_CHARS   = 95
TOTAL_TILES   = TOTAL_CHARS * 2   ; 190 (top + bottom per char)
FONT_BYTES    = TOTAL_TILES * 32  ; 6080 (BG1 4bpp, 32 bytes/tile)

TILEMAP_BYTES = 4096 * 2          ; 8192 (64×64 tilemap)

; -----------------------------------------------------------------------------
; Direct-page variables ($00–$0F, zeroed in init)
; -----------------------------------------------------------------------------

cursor_x        = $00   ; current column (0–31)
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
cursor_y        = $0C   ; current character row (0–31, circular)
top_vram_row    = $0D   ; topmost visible character row (0–31)
addr_scratch    = $0E   ; 16-bit VRAM address scratch ($0E=low, $0F=high)

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
; calc_addr_top — compute VRAM word address for the top tile of the character
;                 at (cursor_x, cursor_y) and store in addr_scratch.
;
; Call with 8-bit A (routine switches to 16-bit internally and restores 8-bit).
; X register width is not changed.
;
; Formula:
;   tile_row_top = cursor_y * 2
;   addr = screen_y_off + screen_x_off + (tile_row_top & $1F) * 32 + (cursor_x & $1F)
;   screen_y_off = $0800 if cursor_y >= 16, else $0000
;   screen_x_off = $0400 if cursor_x >= 32, else $0000
;
; addr_bot = addr_scratch + 32  (caller adds $0020 when needed)
; =============================================================================

calc_addr_top:
    rep  #$20
    .a16

    ; screen_y_off: $0800 when cursor_y >= 16
    lda  cursor_y
    and  #$00FF
    cmp  #$0010
    bcc  @no_y_off
    lda  #$0800
    bra  @y_off_done
@no_y_off:
    lda  #$0000
@y_off_done:
    sta  addr_scratch

    ; (tile_row_top & $1F) * 32  =  (cursor_y * 2 & $1F) << 5
    lda  cursor_y
    and  #$00FF
    asl                  ; * 2 = tile_row_top (0–62)
    and  #$001F          ; & $1F (0–30)
    asl
    asl
    asl
    asl
    asl                  ; * 32
    clc
    adc  addr_scratch
    sta  addr_scratch

    ; screen_x_off: $0400 when cursor_x >= 32
    lda  cursor_x
    and  #$00FF
    cmp  #$0020
    bcc  @no_x_off
    lda  addr_scratch
    clc
    adc  #$0400
    sta  addr_scratch
@no_x_off:

    ; + (cursor_x & $1F)
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
    ; Zero direct-page variables $00–$0F
    ; -------------------------------------------------------------------------
    ldx     #$000F
    .i16
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
    ; DMA 2 — BG1 4bpp font tiles → VRAM word $1000 (byte $2000)
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
    stz     SETINI              ; no hi-res
    lda     #$01                ; Mode 1, 8×8 tiles
    sta     BGMODE
    lda     #$03                ; tilemap at VRAM $0000, 64×64
    sta     BG1SC
    lda     #$01                ; BG1 tiles at word $1000 (byte $2000)
    sta     BG12NBA
    lda     #$01                ; BG1 on main screen
    sta     TM
    stz     TMW                 ; nothing on sub screen

    ; Scroll = 0
    stz     BG1HOFS
    stz     BG1HOFS
    stz     BG1VOFS
    stz     BG1VOFS

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
    ; Normal character write — two tiles (top + bottom)
    ; -----------------------------------------------------------------------
@normal_tile:
    lda     cursor_x
    cmp     #32
    bcc     :+
    jmp     @no_pending          ; line full, newline still pending
:
    jsr     calc_addr_top        ; → addr_scratch = addr of top tile

    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL               ; sets VMADDL + VMADDH (16-bit write)
    sep     #$20
    .a8

    lda     pending_tile_lo      ; tile_top = C * 2
    sta     VMDATAL
    lda     pending_tile_hi      ; = $00 for normal chars
    sta     VMDATAH

    ; bottom tile at addr_scratch + 32
    rep     #$20
    .a16
    lda     addr_scratch
    clc
    adc     #$0020
    sta     VMADDL
    sep     #$20
    .a8

    lda     pending_tile_lo
    inc     a                    ; tile_bot = tile_top + 1
    sta     VMDATAL
    lda     pending_tile_hi
    sta     VMDATAH

    ; advance cursor; 32 = line full → queue newline for next VBlank
    inc     cursor_x
    lda     cursor_x
    cmp     #32
    bcs     :+
    jmp     @no_pending
:
    ; line full → queue KEY_ENTER sentinel ($FFFE)
    lda     #$FE
    sta     pending_tile_lo
    lda     #$FF
    sta     pending_tile_hi
    lda     #$01
    sta     pending_flag
    jmp     @no_pending

    ; -----------------------------------------------------------------------
    ; KEY_DELETE — erase last character (both top + bottom tiles → tile 0)
    ; -----------------------------------------------------------------------
@do_delete:
    lda     cursor_x
    bne     :+
    jmp     @no_pending
:
    dec     cursor_x
    jsr     calc_addr_top

    rep     #$20
    .a16
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    stz     VMDATAL              ; tile 0 = space (top)
    stz     VMDATAH

    rep     #$20
    .a16
    lda     addr_scratch
    clc
    adc     #$0020
    sta     VMADDL
    sep     #$20
    .a8
    stz     VMDATAL              ; tile 0 = space (bottom)
    stz     VMDATAH
    jmp     @no_pending

    ; -----------------------------------------------------------------------
    ; KEY_ENTER — advance to next character row, scroll viewport if needed,
    ;             clear the new row in VRAM (128 tile writes in 4 sections)
    ; -----------------------------------------------------------------------
@do_newline:
    ; cursor_y = (cursor_y + 1) & $1F
    lda     cursor_y
    inc     a
    and     #$1F
    sta     cursor_y

    ; visible_offset = (cursor_y - top_vram_row) & $1F
    ; if >= 14: scroll viewport
    sec
    sbc     top_vram_row
    and     #$1F
    cmp     #14
    bcc     @newline_no_scroll

    ; top_vram_row = (cursor_y - 13) & $1F
    lda     cursor_y
    sec
    sbc     #13
    and     #$1F
    sta     top_vram_row

    ; BG1VOFS = top_vram_row * 16  (max 31*16 = 496 = $01F0, 9 bits)
    rep     #$20
    .a16
    lda     top_vram_row
    and     #$00FF
    asl
    asl
    asl
    asl                          ; * 16
    sep     #$20
    .a8
    sta     BG1VOFS              ; low byte
    xba
    sta     BG1VOFS              ; high byte
@newline_no_scroll:

    ; --- Clear new VRAM row: 4 sections of 32 sequential writes each ---
    ; tile_row_top = cursor_y * 2
    ; Section A: top tile row,    cols  0–31  → screen 0 or 2
    ; Section B: top tile row,    cols 32–63  → screen 1 or 3  (+$400)
    ; Section C: bottom tile row, cols  0–31  → +32 from A
    ; Section D: bottom tile row, cols 32–63  → +$400 from C

    rep     #$20
    .a16
    lda     cursor_y
    and     #$00FF
    asl                          ; tile_row_top = cursor_y * 2
    pha                          ; save tile_row_top

    and     #$001F               ; tile_row_top & $1F
    asl
    asl
    asl
    asl
    asl                          ; * 32  = row offset within screen
    sta     addr_scratch

    pla                          ; restore tile_row_top
    cmp     #$0020               ; tile_row_top >= 32?
    bcc     @cl_no_y
    lda     addr_scratch
    clc
    adc     #$0800
    sta     addr_scratch
@cl_no_y:
    ; addr_scratch = Section A base (top tile row, left half)

    ; Section A
    lda     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    ldx     #32
@cl_A:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_A

    ; Section B (+$400)
    rep     #$20
    .a16
    lda     addr_scratch
    clc
    adc     #$0400
    sta     VMADDL
    sep     #$20
    .a8
    ldx     #32
@cl_B:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_B

    ; Section C (bottom tile row, left half = addr_scratch + 32)
    rep     #$20
    .a16
    lda     addr_scratch
    clc
    adc     #$0020
    sta     addr_scratch
    sta     VMADDL
    sep     #$20
    .a8
    ldx     #32
@cl_C:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_C

    ; Section D (bottom tile row, right half = addr_scratch + $400)
    rep     #$20
    .a16
    lda     addr_scratch
    clc
    adc     #$0400
    sta     VMADDL
    sep     #$20
    .a8
    ldx     #32
@cl_D:
    stz     VMDATAL
    stz     VMDATAH
    dex
    bne     @cl_D

    stz     cursor_x
    bra     @no_pending          ; fall through to @no_pending

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
    .byte "SNES TERMINAL        "
    .byte $20                    ; map mode: LoROM, SlowROM
    .byte $00                    ; cartridge type: ROM only
    .byte $08                    ; ROM size exponent: 32 KiB image
    .byte $00                    ; SRAM size: none
    .byte $02                    ; destination code: Europe (PAL)
    .byte $00                    ; old licensee code (Nintendo)
    .byte $00                    ; version
    .word $FFFF                  ; checksum complement (placeholder)
    .word $0000                  ; checksum (placeholder)

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
